import base64
import copy
import io
import json
import re
import struct
import unittest
import zlib
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import psycopg2
from PIL import Image

from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user

from ..models.project_task_image_mixin import get_image_source
from .common import ProjectAiDrivenCommon
from odoo.addons.html_editor.models import html_field_history_mixin

# 1x1 PNG images of four colours
RED = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
BLUE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
GREEN = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaGAAAAEEAIFw9selAAAAAElFTkSuQmCC"
YELLOW = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4/58BAAT/Af9dfQKHAAAAAElFTkSuQmCC"
SVG = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="
MARKUP = base64.b64encode(b"<html>").decode()
SOURCE_PATTERN = re.compile(r'src="(/web/image/(\d+)\?access_token=([\w-]+))"')
WRITTEN_BEFORE = datetime(2001, 2, 3, 4, 5, 6)
# the field a line of a tracking message body names
TRACKED_FIELD_PATTERN = re.compile(r"<i>\((.*?)\)</i>")


def png_data(width, height):
    """A base64 PNG of width x height RGB pixels, none of them given."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def tiff_data(width, height, strip):
    """A base64 TIFF of width x height grey pixels in one deflate strip."""
    short_type, long_type = 3, 4
    strip_offset = 8 + 2 + 9 * 12 + 4
    entries = (
        (256, long_type, width),
        (257, long_type, height),
        (258, short_type, 8),
        (259, short_type, 8),
        (262, short_type, 1),
        (273, long_type, strip_offset),
        (277, short_type, 1),
        (278, long_type, height),
        (279, long_type, len(strip)),
    )
    # little-endian: a short value fills the first two bytes of its four
    ifd = struct.pack("<H", len(entries)) + b"".join(
        struct.pack("<HHII", tag, kind, 1, value) for tag, kind, value in entries
    )
    return base64.b64encode(b"II*\x00" + struct.pack("<I", 8) + ifd + struct.pack("<I", 0) + strip).decode()


def data_uri(data, mimetype="image/png"):
    return "data:%s;base64,%s" % (mimetype, data)


def image_tag(source, attributes=""):
    return '<img class="img-fluid" src="%s"%s>' % (source, attributes)


def image_html(*tags):
    """Html as the sanitizer stores it, so that only a src can differ."""
    return "<p>Before &amp; after</p><p>%s</p><p>End</p>" % "".join(tags)


class TestInlineImages(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_project = cls.env["project.project"].create({"name": "Not AI-driven"})

    def _attachments(self, record):
        return self.env["ir.attachment"].search([("res_model", "=", record._name), ("res_id", "=", record.id)])

    def _get_sources(self, text):
        """[(src, attachment)] of the attachment images of text, in order."""
        return [
            (source, self.env["ir.attachment"].browse(int(attachment_id)))
            for source, attachment_id, _token in SOURCE_PATTERN.findall(text or "")
        ]

    def assertMovedTo(self, text, html, images, record):
        """text is html with each inline image of images (data: attachment name)
        replaced by the link of an attachment of record holding it, and nothing
        else changed.
        """
        self.assertNotIn("data:image", text)
        expected_text = html
        for data, name in images:
            attachment = self._attachments(record).filtered(
                lambda attachment, data=data: attachment.raw.to_base64() == data
            )
            self.assertEqual(len(attachment), 1, "one attachment of the record per image")
            self.assertEqual(attachment.name, name)
            self.assertEqual(attachment.mimetype, "image/png")
            self.assertTrue(attachment.access_token)
            expected_text = expected_text.replace(
                data_uri(data), "/web/image/%s?access_token=%s" % (attachment.id, attachment.access_token)
            )
        self.assertEqual(text, expected_text)

    @contextmanager
    def _count_attachment_calls(self):
        model_cls = type(self.env["ir.attachment"])
        calls = []

        def counted(method_name):
            method = getattr(model_cls, method_name)

            def call(records, *args, **kwargs):
                calls.append(method_name)
                return method(records, *args, **kwargs)

            return call

        with (
            patch.object(model_cls, "create", counted("create")),
            patch.object(model_cls, "_search", counted("_search")),
            patch.object(model_cls, "exists", counted("exists")),
        ):
            yield calls

    def _store_as_before(self, record, fname, text):
        """Store text as a save before this version did: with its inline images."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE %s SET %s = %%s WHERE id = %%s" % (record._table, fname), [text, record.id])
        record.invalidate_recordset([fname])

    def _new_task(self, **values):
        return self.env["project.task"].create({"name": "Pasted", "project_id": self.project.id, **values})

    def test_create_moves_inline_images_of_description_and_solution(self):
        description = image_html(image_tag(data_uri(RED), ' style="width: 50%;"'))
        solution = image_html(image_tag(data_uri(BLUE)))
        task = self._new_task(description=description, solution=solution)
        self.assertMovedTo(task.description, description, [(RED, "image-1.png")], task)
        self.assertMovedTo(task.solution, solution, [(BLUE, "image-2.png")], task)
        self.assertEqual(len(self._attachments(task)), 2)
        self.env.flush_all()
        self.env.cr.execute("SELECT description FROM project_task WHERE id = %s", [task.id])
        self.assertEqual(self.env.cr.fetchone()[0], task.description, "the stored text is the moved one")

    def test_write_moves_inline_images(self):
        description = image_html(image_tag(data_uri(RED)))
        comments = image_html(image_tag(data_uri(BLUE), ' alt="Result"'))
        self.task.write({"description": description, "testcase_general_comments": comments})
        self.assertMovedTo(self.task.description, description, [(RED, "image-1.png")], self.task)
        self.assertMovedTo(self.task.testcase_general_comments, comments, [(BLUE, "Result")], self.task)

    def test_name_from_data_filename_then_alt_then_numbered(self):
        description = image_html(
            image_tag(data_uri(RED), ' data-filename="shot.png" alt="Ignored"'),
            image_tag(data_uri(BLUE), ' alt="Diagram"'),
            image_tag(data_uri(GREEN), ' alt=" "'),
        )
        solution = image_html(image_tag(data_uri(YELLOW), ' data-file-name="paste.png"'))
        self.task.write({"description": description, "solution": solution})
        self.assertMovedTo(
            self.task.description,
            description,
            [(RED, "shot.png"), (BLUE, "Diagram"), (GREEN, "image-1.png")],
            self.task,
        )
        self.assertMovedTo(self.task.solution, solution, [(YELLOW, "paste.png")], self.task)

    def test_two_images_and_the_same_image_twice(self):
        description = image_html(image_tag(data_uri(RED)), image_tag(data_uri(BLUE)), image_tag(data_uri(RED)))
        self.task.description = description
        self.assertMovedTo(self.task.description, description, [(RED, "image-1.png"), (BLUE, "image-2.png")], self.task)
        sources = self._get_sources(self.task.description)
        self.assertEqual(sources[0], sources[2])
        self.assertEqual(len(self._attachments(self.task)), 2)

    def test_broken_inline_image_stays_and_the_save_passes(self):
        broken_tags = (
            image_tag(data_uri("AAA")),
            image_tag(data_uri("not*base64")),
            image_tag(data_uri("")),
        )
        description = image_html(*broken_tags, image_tag(data_uri(RED)))
        self.task.description = description
        self.assertEqual(
            self.task.description,
            description.replace(data_uri(RED), self._get_sources(self.task.description)[0][0]),
            "the broken images stay as they are, the good one is moved",
        )
        self.assertEqual(len(self._attachments(self.task)), 1)
        self.assertEqual(self._attachments(self.task).name, "image-1.png", "a broken image takes no number")

    def test_resource_limits_and_database_errors_fail_the_save(self):
        description = image_html(image_tag(data_uri(RED)))
        cpu_limit = Exception("CPU time limit exceeded.")
        for error in (MemoryError(), cpu_limit, psycopg2.OperationalError("connection lost")):
            with (
                self.subTest(error=error),
                patch.object(type(self.env["ir.attachment"]), "_check_contents", side_effect=error),
            ):
                with self.assertRaises(type(error)) as raised:
                    self.task.description = description
                self.assertIs(raised.exception, error)

    def test_save_leaves_its_precommit_hooks_to_the_commit(self):
        def refuse_commit():
            raise UserError("Refused on commit")

        self.env.cr.precommit.add(refuse_commit)
        self.task.description = image_html(image_tag(data_uri(RED)))
        self.assertTrue(self._attachments(self.task), "the image is moved")
        # not Odoo's assertRaisesRegex: its savepoint flushes, which runs the hook before the block
        with unittest.TestCase.assertRaisesRegex(self, UserError, "Refused on commit"):
            self.env.cr.precommit.run()

    def test_save_moving_an_image_is_tracked_with_its_transaction(self):
        self._flush_tracking()
        messages_before = self.task.message_ids
        self.task.write({"name": "Renamed", "description": image_html(image_tag(data_uri(RED)))})
        self.task.resolution = "data"
        self._flush_tracking()
        tracking_messages = (self.task.message_ids - messages_before).filtered(
            lambda message: message.message_type == "tracking"
        )
        self.assertEqual(len(tracking_messages), 1, "one tracking message for the transaction")
        self.assertEqual(
            set(TRACKED_FIELD_PATTERN.findall(tracking_messages.body)),
            {self.task._fields["name"].string, self.task._fields["resolution"].string},
        )

    def test_the_description_history_holds_the_moved_text(self):
        """The history mixin builds a revision from the text the save stores:
        the image mixin comes below it in the task's classes.
        """
        with patch.object(
            html_field_history_mixin, "generate_patch", side_effect=html_field_history_mixin.generate_patch
        ) as generate_patch:
            self.task.description = image_html(image_tag(data_uri(RED)))
        moved_description = self.task.description
        new_content, _old_content = generate_patch.call_args.args
        self.assertEqual(new_content, moved_description)
        self.assertNotIn("data:image", new_content)
        self.task.description = "<p>Rewritten</p>"
        latest_revision = self.task.html_field_history["description"][0]
        self.assertEqual(
            self.task.html_field_history_get_content_at_revision("description", latest_revision["revision_id"]),
            moved_description,
        )

    def assertStaysInline(self, source):
        """A save of an inline image core fails to read passes and leaves the
        image inline, on write and on create.
        """
        description = image_html(image_tag(source))
        self.task.description = description
        task = self._new_task(description=description)
        for record in (self.task, task):
            self.assertEqual(record.description, description, "the image stays inline")
            self.assertFalse(self._attachments(record))

    def test_inline_image_too_large_to_open_stays(self):
        self.assertStaysInline(data_uri(png_data(20000, 20000)))

    def test_an_image_core_stores_without_reading_it_is_moved(self):
        """Core stores markup given as an image and a truncated tiff as they
        come, without resizing them: they are moved as any image core accepts.
        """
        tiff = tiff_data(3000, 3000, zlib.compress(bytes(3000 * 3000))[:50])
        self.task.description = image_html(image_tag(data_uri(MARKUP)), image_tag(data_uri(tiff, "image/tiff")))
        sources = self._get_sources(self.task.description)
        self.assertEqual(self.task.description, image_html(*(image_tag(source) for source, _attachment in sources)))
        self.assertEqual(
            [(attachment.raw.to_base64(), attachment.mimetype) for _source, attachment in sources],
            [(MARKUP, "image/png"), (tiff, "image/tiff")],
        )

    def test_core_resizes_an_image_once_on_save_and_on_copy(self):
        self.env["ir.config_parameter"].set_str("base.image_autoresize_max_px", "2x2")
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
        attachment_model = type(self.env["ir.attachment"])
        with patch.object(
            attachment_model,
            "_postprocess_contents",
            autospec=True,
            side_effect=attachment_model._postprocess_contents,
        ) as postprocess:
            self.task.description = image_html(image_tag(data_uri(base64.b64encode(buffer.getvalue()).decode())))
            self.assertEqual(postprocess.call_count, 1)
            task_copy = self.task.copy()
            self.assertEqual(postprocess.call_count, 2)
        for task in (self.task, task_copy):
            [(_source, attachment)] = self._get_sources(task.description)
            self.assertEqual(attachment, self._attachments(task))
            self.assertEqual(Image.open(io.BytesIO(bytes(attachment.raw))).size, (2, 2))

    def test_upper_case_and_escaped_schemes_are_moved(self):
        self.task.description = image_html(
            image_tag("DATA:IMAGE/PNG;BASE64,%s" % RED), image_tag("data&#58;image/png;base64,%s" % BLUE)
        )
        sources = self._get_sources(self.task.description)
        self.assertEqual(self.task.description, image_html(*(image_tag(source) for source, _attachment in sources)))
        self.assertEqual([attachment.raw.to_base64() for _source, attachment in sources], [RED, BLUE])
        self.assertEqual({attachment.mimetype for _source, attachment in sources}, {"image/png"})

    def test_image_linked_by_url_stays(self):
        description = image_html(image_tag("https://example.com/a.png"))
        self.task.description = description
        self.assertEqual(self.task.description, description)
        self.assertFalse(self._attachments(self.task))

    def test_task_of_a_project_not_ai_driven(self):
        description = image_html(image_tag(data_uri(RED)))
        task = self.env["project.task"].create(
            {"name": "Elsewhere", "project_id": self.other_project.id, "description": description}
        )
        self.assertFalse(task.is_ai_driven)
        self.assertMovedTo(task.description, description, [(RED, "image-1.png")], task)

    def test_a_line_keeps_its_images_as_its_own_attachments(self):
        answer = image_html(image_tag(data_uri(RED)))
        expected_results = image_html(image_tag(data_uri(BLUE)))
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "question": "<p>Which screen?</p>", "answer": answer}
        )
        testcase = self.env["project.task.testcase"].create({"task_id": self.task.id, "name": "Shows it"})
        testcase.expected_results = expected_results
        self.assertMovedTo(question.answer, answer, [(RED, "image-1.png")], question)
        self.assertMovedTo(testcase.expected_results, expected_results, [(BLUE, "image-1.png")], testcase)
        self.assertFalse(self._attachments(self.task), "no attachment of the task")

    def test_lines_created_with_the_task(self):
        steps = image_html(image_tag(data_uri(RED)))
        task = self._new_task(testcase_ids=[Command.create({"name": "Shows it", "steps_to_execute": steps})])
        self.assertMovedTo(task.testcase_ids.steps_to_execute, steps, [(RED, "image-1.png")], task.testcase_ids)

    def test_project_user_gets_the_attachments(self):
        description = image_html(image_tag(data_uri(RED)))
        answer = image_html(image_tag(data_uri(BLUE)))
        task = self.task.with_user(self.user_dev)
        task.description = description
        question = (
            self.env["project.task.question"].with_user(self.user_dev).create({"task_id": task.id, "answer": answer})
        )
        self.assertMovedTo(task.description, description, [(RED, "image-1.png")], self.task)
        self.assertMovedTo(question.answer, answer, [(BLUE, "image-1.png")], question)
        attachments = self._attachments(self.task) | self._attachments(question)
        self.assertEqual(attachments.mapped("create_uid"), self.user_dev)
        self.assertTrue(attachments.with_user(self.user_dev).has_access("read"))

    def test_reader_of_a_task_cannot_attach_through_it(self):
        self._restrict_task_access("Keep the task", "[('name', '!=', 'Read-only Task')]", operation="ud")
        self.task.name = "Read-only Task"
        self.task.with_user(self.user_dev).check_access("read")
        html = image_html(image_tag(data_uri(RED)))
        with self._count_attachment_calls() as calls:
            with self.assertRaises(AccessError):
                self.task.with_user(self.user_dev).write({"description": html})
            with self.assertRaises(AccessError):
                self.env["project.task.question"].with_user(self.user_dev).create(
                    {"task_id": self.task.id, "answer": html}
                )
        self.assertNotIn("create", calls, "a refused save creates no attachment")

    def test_svg_follows_the_attachment_rules_of_core(self):
        """The sanitizer empties an svg data src on save: only a text saved
        before can hold one, and a user who may not edit views gets it as text.
        """
        svg_html = image_html(image_tag(data_uri(SVG, "image/svg+xml")))
        task_copy = self.task.copy()
        self._store_as_before(self.task, "description", svg_html)
        self._store_as_before(task_copy, "description", svg_html)
        self.task.with_user(self.user_dev)._move_inline_images_to_attachments()
        task_copy._move_inline_images_to_attachments()
        self.assertEqual(self._attachments(self.task).mimetype, "text/plain")
        self.assertEqual(self._attachments(task_copy).mimetype, "image/svg+xml")

    def test_copy_gets_its_own_attachments(self):
        description = image_html(image_tag(data_uri(RED), ' alt="Diagram"'))
        answer = image_html(image_tag(data_uri(BLUE)))
        expected_results = image_html(image_tag(data_uri(GREEN)))
        self.task.description = description
        question = self.env["project.task.question"].create({"task_id": self.task.id, "answer": answer})
        testcase = self.env["project.task.testcase"].create(
            {"task_id": self.task.id, "name": "Shows it", "expected_results": expected_results}
        )
        source_attachments = self._attachments(self.task) | self._attachments(question) | self._attachments(testcase)
        source_description = self.task.description

        task_copy = self.task.copy()

        self.assertEqual(self.task.description, source_description)
        self.assertMovedTo(task_copy.description, description, [(RED, "Diagram")], task_copy)
        self.assertMovedTo(task_copy.question_ids.answer, answer, [(BLUE, "image-1.png")], task_copy.question_ids)
        self.assertMovedTo(
            task_copy.testcase_ids.expected_results,
            expected_results,
            [(GREEN, "image-1.png")],
            task_copy.testcase_ids,
        )
        copied_attachments = (
            self._attachments(task_copy)
            | self._attachments(task_copy.question_ids)
            | self._attachments(task_copy.testcase_ids)
        )
        self.assertEqual(len(copied_attachments), 3)
        self.assertFalse(
            set(copied_attachments.mapped("access_token")) & set(source_attachments.mapped("access_token"))
        )
        self.task.unlink()
        self.assertEqual(copied_attachments.exists(), copied_attachments)
        self.assertEqual({attachment.raw.to_base64() for attachment in copied_attachments}, {RED, BLUE, GREEN})

    def _store_missing_file(self, attachment):
        """Point attachment at a file the filestore does not hold."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE ir_attachment SET store_fname = %s, db_datas = NULL WHERE id = %s",
            ["missing/project_ai_driven_%d" % attachment.id, attachment.id],
        )
        attachment.invalidate_recordset()

    def test_a_copy_keeps_the_link_of_an_image_whose_file_is_missing(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        [(_source, attachment)] = self._get_sources(self.task.description)
        self._store_missing_file(attachment)
        self.assertFalse(attachment.raw)

        task_copy = self.task.copy()

        self.assertEqual(task_copy.description, self.task.description)
        self.assertFalse(self._attachments(task_copy))

    def test_a_copy_keeps_the_link_of_an_image_core_fails_to_read(self):
        attachment = (
            self.env["ir.attachment"]
            .with_context(image_no_postprocess=True)
            .create(
                {
                    "name": "huge.png",
                    "raw": base64.b64decode(png_data(20000, 20000)),
                    "mimetype": "image/png",
                    "res_model": "project.task",
                    "res_id": self.task.id,
                }
            )
        )
        attachment.generate_access_token()
        self.task.description = image_html(image_tag(get_image_source(attachment)))

        task_copy = self.task.copy()

        self.assertEqual(task_copy.description, self.task.description)
        self.assertFalse(self._attachments(task_copy))

    def test_deleting_a_task_deletes_the_images_of_its_rows(self):
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": image_html(image_tag(data_uri(BLUE)))}
        )
        testcase = self.env["project.task.testcase"].create(
            {"task_id": self.task.id, "name": "Shows it", "comments": image_html(image_tag(data_uri(GREEN)))}
        )
        row_attachments = self._attachments(question) | self._attachments(testcase)
        self.assertEqual(len(row_attachments), 2)

        self.task.with_user(self.user_lead).unlink()

        self.assertFalse(row_attachments.exists())

    def test_deleting_a_task_deletes_the_images_of_the_rows_of_its_sub_tasks(self):
        sub_task = self._new_task(name="Sub-task", parent_id=self.task.id)
        testcase = self.env["project.task.testcase"].create(
            {"task_id": sub_task.id, "name": "Shows it", "comments": image_html(image_tag(data_uri(GREEN)))}
        )
        row_attachments = self._attachments(testcase)
        self.assertEqual(len(row_attachments), 1)

        self.task.with_user(self.user_lead).unlink()

        self.assertFalse(sub_task.exists())
        self.assertFalse(row_attachments.exists())

    def test_rows_go_with_the_sub_tasks_core_deletes_while_an_archiving_is_pending(self):
        for archived_before in (False, True):
            with self.subTest(archived_before=archived_before):
                parent = self._new_task(name="Parent")
                # a row of the parent too: its deletion flushes before core reads the sub-tasks
                self.env["project.task.question"].create(
                    {"task_id": parent.id, "answer": image_html(image_tag(data_uri(BLUE)))}
                )
                sub_task = self._new_task(name="Sub-task", parent_id=parent.id, active=not archived_before)
                testcase = self.env["project.task.testcase"].create(
                    {"task_id": sub_task.id, "name": "Shows it", "comments": image_html(image_tag(data_uri(GREEN)))}
                )
                row_attachments = self._attachments(testcase)
                self.env.flush_all()
                # changed in memory only: the sub-tasks core deletes are read from the database
                sub_task.active = archived_before

                parent.with_user(self.user_lead).unlink()

                self.assertEqual(bool(testcase.exists()), bool(sub_task.exists()))
                self.assertEqual(bool(row_attachments.exists()), bool(sub_task.exists()))

    def test_a_deletion_refused_for_a_sub_task_leaves_the_rows_of_every_task(self):
        hidden_project = self.env["project.project"].create(
            {"name": "Followers only", "privacy_visibility": "followers"}
        )
        sub_task = self._new_task(name="Hidden sub-task", parent_id=self.task.id, project_id=hidden_project.id)
        sub_task.message_unsubscribe(partner_ids=self.user_lead.partner_id.ids)
        testcase = self.env["project.task.testcase"].create(
            {"task_id": sub_task.id, "name": "Shows it", "comments": image_html(image_tag(data_uri(GREEN)))}
        )
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": image_html(image_tag(data_uri(BLUE)))}
        )
        row_attachments = self._attachments(testcase) | self._attachments(question)
        self.assertEqual(len(row_attachments), 2)
        self.assertFalse(sub_task.with_user(self.user_lead).has_access("unlink"))
        self.assertTrue(self.task.with_user(self.user_lead).has_access("unlink"))

        # in place, without the savepoint of Odoo's assertRaises: a caller catching the error keeps what ran
        with unittest.TestCase.assertRaises(self, AccessError):
            self.task.with_user(self.user_lead).unlink()

        self.assertTrue(testcase.exists())
        self.assertTrue(question.exists())
        self.assertEqual(row_attachments.exists(), row_attachments)

    def test_a_refused_deletion_of_a_task_leaves_its_rows_and_their_images(self):
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": image_html(image_tag(data_uri(BLUE)))}
        )
        question_attachment = self._attachments(question)

        with unittest.TestCase.assertRaises(self, AccessError):
            self.task.with_user(internal_user).unlink()

        self.assertTrue(question.exists())
        self.assertTrue(question_attachment.exists())

    def test_a_task_from_a_template_gets_its_own_attachments(self):
        template = self._new_task(name="Template", is_template=True, description=image_html(image_tag(data_uri(RED))))
        task = self.env["project.task"].browse(template.action_create_from_template())
        self.assertNotEqual(task, template)
        self.assertMovedTo(task.description, image_html(image_tag(data_uri(RED))), [(RED, "image-1.png")], task)

    def test_a_line_created_from_another_record_s_text_copies_its_images(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        (task_source, task_attachment) = self._get_sources(self.task.description)[0]
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": image_html(image_tag(task_source))}
        )
        (question_source, question_attachment) = self._get_sources(question.answer)[0]
        self.assertEqual(question_attachment, self._attachments(question))
        self.assertNotEqual(question_attachment, task_attachment)
        self.assertEqual(question_attachment.raw.to_base64(), RED)

        testcase = self.env["project.task.testcase"].create(
            {"task_id": self.task.id, "name": "Shows it", "comments": image_html(image_tag(question_source))}
        )
        self.assertEqual(self._get_sources(testcase.comments)[0][1], self._attachments(testcase))

    def test_create_copies_only_images_its_user_may_see(self):
        hidden_project = self.env["project.project"].create({"name": "Hidden", "privacy_visibility": "followers"})
        hidden_task = self.env["project.task"].create(
            {"name": "Hidden", "project_id": hidden_project.id, "description": image_html(image_tag(data_uri(RED)))}
        )
        self.task.description = image_html(image_tag(data_uri(BLUE)))
        hidden_attachment = self._attachments(hidden_task)
        with self.assertRaises(AccessError):
            hidden_task.with_user(self.user_dev).read(["name"])
        hidden_link = image_tag("/web/image/%s" % hidden_attachment.id)
        readable_link = image_tag("/web/image/%s-%s/x.png" % (self._attachments(self.task).id, "1a2b3c4d"))
        token_link = image_tag(
            "/web/image/%s?unique=1&amp;access_token=%s" % (hidden_attachment.id, hidden_attachment.access_token)
        )
        description = image_html(hidden_link, readable_link, token_link)

        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .create({"name": "Pasted over the API", "project_id": self.project.id, "description": description})
        )

        sources = self._get_sources(task.description)
        self.assertEqual(len(sources), 2, "the image of a task its user cannot read stays a link, not a copy")
        readable_source, token_source = sources
        self.assertEqual(
            task.description, image_html(hidden_link, image_tag(readable_source[0]), image_tag(token_source[0]))
        )
        self.assertEqual(readable_source[1].raw.to_base64(), BLUE)
        self.assertEqual(token_source[1].raw.to_base64(), RED)
        self.assertEqual(self._attachments(task), readable_source[1] | token_source[1])

    def test_create_as_superuser_copies_only_links_with_their_token(self):
        other_task = self.env["project.task"].create(
            {"name": "Other", "project_id": self.other_project.id, "description": image_html(image_tag(data_uri(RED)))}
        )
        attachment = self._attachments(other_task)
        foreign_links = (
            image_tag("/web/image/%s" % attachment.id),
            image_tag("/web/image/%s?access_token=%s" % (attachment.id, "0" * len(attachment.access_token))),
        )
        token_link = image_tag("/web/image/%s?access_token=%s" % (attachment.id, attachment.access_token))

        # sudo for a form posted by anyone, uid 1 for a cron
        for task_model in (
            self.env["project.task"].with_user(self.user_dev).sudo(),
            self.env["project.task"].with_user(SUPERUSER_ID),
        ):
            with self.subTest(uid=task_model.env.uid):
                task = task_model.create(
                    {
                        "name": "Posted by a form",
                        "project_id": self.project.id,
                        "description": image_html(*foreign_links, token_link),
                    }
                )

                task_attachment = self._attachments(task)
                self.assertEqual(len(task_attachment), 1)
                self.assertEqual(task_attachment.raw.to_base64(), RED)
                self.assertEqual(
                    task.description,
                    image_html(*foreign_links, image_tag(get_image_source(task_attachment))),
                    "only the link with the attachment's own token is copied",
                )

    def test_a_line_moved_to_another_task_copies_the_images_of_other_records(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        task_source = self._get_sources(self.task.description)[0][0]
        answer = image_html(image_tag(data_uri(BLUE)))
        question = self.env["project.task.question"].create({"task_id": self.task.id, "answer": answer})
        own_source = self._get_sources(question.answer)[0][0]
        testcase = self.env["project.task.testcase"].create({"task_id": self.task.id, "name": "Shows it"})
        testcase.steps_to_execute = image_html(image_tag(task_source))
        self.assertEqual(testcase.steps_to_execute, image_html(image_tag(task_source)), "a write keeps the link")
        other_task = self.env["project.task"].create({"name": "Other", "project_id": self.project.id})

        question.with_user(self.user_dev).task_id = other_task
        testcase.task_id = other_task

        self.assertEqual(question.answer, image_html(image_tag(own_source)), "its own image stays its own")
        (testcase_source, testcase_attachment) = self._get_sources(testcase.steps_to_execute)[0]
        self.assertNotEqual(testcase_source, task_source)
        self.assertEqual(testcase_attachment, self._attachments(testcase))
        self.task.unlink()
        self.assertEqual(testcase_attachment.exists().raw.to_base64(), RED)

    def test_write_of_a_link_to_another_task_keeps_it(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        source = self._get_sources(self.task.description)[0][0]
        other_task = self.env["project.task"].create({"name": "Other", "project_id": self.project.id})
        other_task.description = image_html(image_tag(source))
        self.assertEqual(other_task.description, image_html(image_tag(source)))
        self.assertFalse(self._attachments(other_task))

    def test_write_of_the_same_task_is_not_a_move(self):
        other_task = self.env["project.task"].create(
            {"name": "Other", "project_id": self.project.id, "description": image_html(image_tag(data_uri(RED)))}
        )
        answer = image_html(image_tag(self._get_sources(other_task.description)[0][0]))
        question = self.env["project.task.question"].create({"task_id": self.task.id})
        question.answer = answer
        question.write({"task_id": self.task.id})
        self.assertEqual(question.answer, answer, "the link to the other task stays")
        self.assertFalse(self._attachments(question))

    def test_save_without_html_fields_does_not_look_for_them(self):
        with patch.object(type(self.task), "_get_image_fields", return_value=[]) as get_image_fields:
            self.task.write({"name": "Renamed", "priority": "1"})
        get_image_fields.assert_not_called()

    def test_write_to_no_record_passes(self):
        no_task = self.env["project.task"].browse()
        self.assertTrue(no_task.write({"x_not_a_field": 1, "description": image_html(image_tag(data_uri(RED)))}))

    def test_recurrence_gives_each_task_its_own_attachments(self):
        self.project.allow_recurring_tasks = True
        description = image_html(image_tag(data_uri(RED)))
        task = self._new_task(
            name="Every Monday",
            description=description,
            recurring_task=True,
            repeat_interval=1,
            repeat_unit="week",
            repeat_type="forever",
        )
        task.state = "1_done"
        next_task = task.recurrence_id.task_ids - task
        self.assertEqual(len(next_task), 1)
        self.assertMovedTo(next_task.description, description, [(RED, "image-1.png")], next_task)
        self.assertNotEqual(next_task.description, task.description)

    def test_create_of_several_tasks_gives_each_its_own_attachments(self):
        description = image_html(image_tag(data_uri(RED)))
        solution = image_html(image_tag(data_uri(BLUE)))
        tasks = self.env["project.task"].create(
            [
                {"name": "First", "project_id": self.project.id, "description": description},
                {"name": "Plain", "project_id": self.project.id, "description": "<p>No image</p>"},
                {"name": "Third", "project_id": self.project.id, "solution": solution},
            ]
        )
        self.assertMovedTo(tasks[0].description, description, [(RED, "image-1.png")], tasks[0])
        self.assertFalse(self._attachments(tasks[1]))
        self.assertMovedTo(tasks[2].solution, solution, [(BLUE, "image-1.png")], tasks[2])

    def test_write_to_several_tasks_gives_each_its_own_attachments(self):
        tasks = self.task | self._new_task(name="Second")
        description = image_html(image_tag(data_uri(RED)))
        tasks.write({"description": description})
        for task in tasks:
            self.assertMovedTo(task.description, description, [(RED, "image-1.png")], task)
        self.assertNotEqual(tasks[0].description, tasks[1].description)

    def test_moving_texts_saved_before_counts_and_returns_them(self):
        description = image_html(image_tag(data_uri(RED)), image_tag(data_uri(RED)))
        answer = image_html(image_tag(data_uri(BLUE)))
        question = self.env["project.task.question"].create({"task_id": self.task.id})
        self._store_as_before(self.task, "description", description)
        self._store_as_before(question, "answer", answer)
        for record in (self.task, question):
            self.env.cr.execute(
                "UPDATE %s SET write_date = %%s WHERE id = %%s" % record._table, [WRITTEN_BEFORE, record.id]
            )
        image_bytes = len(base64.b64decode(RED)) + len(base64.b64decode(BLUE))

        dry_run = self.task._move_inline_images_to_attachments(dry_run=True)

        self.assertEqual(dry_run, {"records": 2, "images": 2, "bytes": image_bytes, "texts": {}})
        self.assertFalse(self._attachments(self.task) | self._attachments(question))
        self.assertEqual(self.task.description, description)

        result = self.task._move_inline_images_to_attachments(store=False)

        new_texts = result.pop("texts")
        self.assertEqual(result, {"records": 2, "images": 2, "bytes": image_bytes})
        self.assertEqual(set(new_texts), {self.task, question})
        self.assertMovedTo(new_texts[self.task]["description"], description, [(RED, "image-1.png")], self.task)
        self.assertMovedTo(new_texts[question]["answer"], answer, [(BLUE, "image-1.png")], question)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self.task.description, description, "store=False leaves the text as it is")
        self.assertEqual(question.answer, answer)
        for record in (self.task, question):
            self.env.cr.execute("SELECT write_date FROM %s WHERE id = %%s" % record._table, [record.id])
            self.assertEqual(self.env.cr.fetchone()[0], WRITTEN_BEFORE, "store=False writes nothing on the record")

    def test_moving_texts_saved_before_counts_only_images_moved(self):
        description = image_html(image_tag(data_uri(png_data(20000, 20000))), image_tag(data_uri(RED)))
        self._store_as_before(self.task, "description", description)
        result = self.task._move_inline_images_to_attachments(store=False)
        self.assertEqual(
            (result["images"], result["bytes"], len(self._attachments(self.task))),
            (1, len(base64.b64decode(RED)), 1),
            "one attachment per image counted",
        )
        new_text = result["texts"][self.task]["description"]
        [(source, attachment)] = self._get_sources(new_text)
        self.assertEqual(new_text, description.replace(data_uri(RED), source), "the image too large stays inline")
        self.assertEqual(attachment.raw.to_base64(), RED)

    def test_dry_run_counts_as_the_run(self):
        description = image_html(image_tag(data_uri(RED)), image_tag(data_uri(png_data(20000, 20000))))
        answer = image_html(image_tag(data_uri(png_data(20000, 20000))))
        question = self.env["project.task.question"].create({"task_id": self.task.id})
        self._store_as_before(self.task, "description", description)
        self._store_as_before(question, "answer", answer)
        expected_counts = {"records": 1, "images": 1, "bytes": len(base64.b64decode(RED))}

        dry_run = self.task._move_inline_images_to_attachments(dry_run=True)
        result = self.task._move_inline_images_to_attachments(store=False)

        self.assertEqual(dry_run, dict(expected_counts, texts={}))
        self.assertEqual(set(result.pop("texts")), {self.task})
        self.assertEqual(result, expected_counts)

    def test_moving_texts_saved_before_covers_the_test_cases(self):
        testcase = self.env["project.task.testcase"].create({"task_id": self.task.id, "name": "Shows it"})
        steps = image_html(image_tag(data_uri(GREEN)))
        self._store_as_before(testcase, "steps_to_execute", steps)

        result = self.task._move_inline_images_to_attachments()

        self.assertEqual((result["records"], result["images"]), (1, 1))
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertMovedTo(testcase.steps_to_execute, steps, [(GREEN, "image-1.png")], testcase)

    def test_moving_texts_saved_before_stores_them(self):
        description = image_html(image_tag(data_uri(RED)))
        self._store_as_before(self.task, "solution", description)
        result = self.task._move_inline_images_to_attachments()
        self.assertEqual(result["records"], 1)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertMovedTo(self.task.solution, description, [(RED, "image-1.png")], self.task)
        self.assertEqual(result["texts"], {self.task: {"solution": self.task.solution}})

    def test_save_without_images_does_not_look_for_attachments(self):
        other_task = self._new_task(description=image_html(image_tag(data_uri(RED))))
        [(source, _attachment)] = self._get_sources(other_task.description)
        link_text = "<p>See %s</p>" % source
        with self._count_attachment_calls() as calls:
            self.task.write({"description": "<p>Plain text</p>", "solution": link_text})
            self.env["project.task.question"].create({"task_id": self.task.id, "answer": link_text})
            self.env.flush_all()
            calls_without_images = list(calls)
            self.task.solution = image_html(image_tag(data_uri(RED)))
            self.env["project.task.question"].create({"task_id": self.task.id, "answer": image_html(image_tag(source))})
        self.assertEqual(calls_without_images, [])
        self.assertIn("create", calls, "a save with an image is counted")
        self.assertIn("exists", calls, "a link looked up is counted")

    def _define_properties(self):
        self.project.task_properties_definition = [
            {"name": "notes_html", "string": "Notes", "type": "html"},
            {"name": "label", "string": "Label", "type": "char"},
        ]

    def _property_values(self, task):
        return task._fields["task_properties"].convert_to_cache(task.task_properties, task, validate=False)

    def test_html_properties_keep_their_images_as_attachments(self):
        self._define_properties()
        notes = image_html(image_tag(data_uri(RED), ' alt="Plan"'))
        label = image_tag(data_uri(BLUE))
        self.task.task_properties = {"notes_html": notes, "label": label}
        self.assertMovedTo(self.task.task_properties["notes_html"], notes, [(RED, "Plan")], self.task)
        self.assertEqual(self.task.task_properties["label"], label, "only an html property is a text with images")

        task = self._new_task(task_properties={"notes_html": image_html(image_tag(data_uri(GREEN)))})
        self.assertMovedTo(
            task.task_properties["notes_html"], image_html(image_tag(data_uri(GREEN))), [(GREEN, "image-1.png")], task
        )
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertNotIn("data:image", self.task.task_properties["notes_html"], "the stored value is the moved one")

    def test_html_property_defaults_keep_their_images_as_attachments(self):
        default_notes = image_html(image_tag(data_uri(RED)))
        self.project.task_properties_definition = [
            {"name": "notes_html", "string": "Notes", "type": "html", "default": default_notes},
        ]
        task = self._new_task()
        moved_task = self.env["project.task"].create({"name": "Moved in", "project_id": self.other_project.id})
        moved_task.project_id = self.project

        self.env.flush_all()
        self.env.invalidate_all()
        for record in task | moved_task:
            with self.subTest(task=record.name):
                self.assertMovedTo(record.task_properties["notes_html"], default_notes, [(RED, "image-1.png")], record)

    def test_html_property_defaults_of_tasks_following_a_parent_into_a_project(self):
        default_notes = image_html(image_tag(data_uri(RED)))
        self.project.task_properties_definition = [
            {"name": "notes_html", "string": "Notes", "type": "html", "default": default_notes},
        ]
        Task = self.env["project.task"]
        parent = Task.create({"name": "Moved parent", "project_id": self.other_project.id})
        sub_task = Task.create({"name": "Sub-task", "parent_id": parent.id})
        sub_sub_task = Task.create({"name": "Sub-sub-task", "parent_id": sub_task.id})
        other_parent = Task.create({"name": "Other parent", "project_id": self.other_project.id})
        given_a_parent = Task.create({"name": "Given a parent", "parent_id": other_parent.id})

        parent.project_id = self.project
        given_a_parent.parent_id = self.task

        self.env.flush_all()
        self.env.invalidate_all()
        for record in parent | sub_task | sub_sub_task | given_a_parent:
            with self.subTest(task=record.name):
                self.assertEqual(record.project_id, self.project)
                self.assertMovedTo(record.task_properties["notes_html"], default_notes, [(RED, "image-1.png")], record)

    def test_html_property_defaults_core_refills_without_a_change_of_project(self):
        Task = self.env["project.task"]
        # created before the default exists: core fills it in on the next write of project_id or parent_id
        same_project = Task.create({"name": "Same project", "project_id": self.project.id})
        under_a_parent = Task.create({"name": "Under a parent", "project_id": self.project.id})
        default_notes = image_html(image_tag(data_uri(RED)))
        self.project.task_properties_definition = [
            {"name": "notes_html", "string": "Notes", "type": "html", "default": default_notes},
        ]

        same_project.project_id = self.project
        under_a_parent.parent_id = self.task

        self.env.flush_all()
        self.env.invalidate_all()
        for record in same_project | under_a_parent:
            with self.subTest(task=record.name):
                self.assertMovedTo(record.task_properties["notes_html"], default_notes, [(RED, "image-1.png")], record)

    def test_html_property_defaults_of_sub_tasks_archived_or_hidden_from_the_writer(self):
        default_notes = image_html(image_tag(data_uri(RED)))
        self.project.task_properties_definition = [
            {"name": "notes_html", "string": "Notes", "type": "html", "default": default_notes},
        ]
        hidden_project = self.env["project.project"].create(
            {"name": "Followers only", "privacy_visibility": "followers"}
        )
        Task = self.env["project.task"]
        parent = Task.create({"name": "Moved parent", "project_id": hidden_project.id})
        parent.message_subscribe(partner_ids=self.user_lead.partner_id.ids)
        hidden_sub_task = Task.create({"name": "Hidden sub-task", "parent_id": parent.id})
        hidden_sub_task.message_unsubscribe(partner_ids=self.user_lead.partner_id.ids)
        archived_sub_task = Task.create({"name": "Archived sub-task", "parent_id": parent.id, "active": False})
        self.assertFalse(hidden_sub_task.with_user(self.user_lead).has_access("read"))

        parent.with_user(self.user_lead).project_id = self.project

        self.env.flush_all()
        self.env.invalidate_all()
        for record in hidden_sub_task | archived_sub_task:
            with self.subTest(task=record.name):
                self.assertEqual(record.project_id, self.project)
                self.assertMovedTo(record.task_properties["notes_html"], default_notes, [(RED, "image-1.png")], record)

    def test_html_property_defaults_of_a_task_a_portal_collaborator_creates(self):
        portal_user = new_test_user(
            self.env, "project_ai_driven_portal_images", groups="base.group_portal", context={"no_reset_password": True}
        )
        default_notes = image_html(image_tag(data_uri(RED)))
        shared_project = self.env["project.project"].create(
            {
                "name": "Shared Project",
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": portal_user.partner_id.id, "access_mode": "edit"})],
                "task_properties_definition": [
                    {"name": "notes_html", "string": "Notes", "type": "html", "default": default_notes},
                ],
            },
        )
        shared_project.message_subscribe(partner_ids=portal_user.partner_id.ids)
        shared_task = self.env["project.task"].create({"name": "Fix the roof", "project_id": shared_project.id})

        task = self.env["project.task"].with_user(portal_user).create({"name": "Leak", "parent_id": shared_task.id})

        task = task.with_env(self.env)
        self.assertMovedTo(task.task_properties["notes_html"], default_notes, [(RED, "image-1.png")], task)

    def test_html_properties_saved_as_the_web_client_sends_them(self):
        self._define_properties()
        notes = image_html(image_tag(data_uri(RED)))
        self.task.task_properties = [
            {"name": "notes_html", "string": "Notes", "type": "html", "value": notes},
            {"name": "label", "string": "Label", "type": "char", "value": "Kept"},
        ]
        self.assertMovedTo(self.task.task_properties["notes_html"], notes, [(RED, "image-1.png")], self.task)
        self.assertEqual(self.task.task_properties["label"], "Kept")

    def test_a_copy_gets_its_own_attachments_of_the_html_properties(self):
        self._define_properties()
        notes = image_html(image_tag(data_uri(RED)))
        self.task.task_properties = {"notes_html": notes, "label": "Kept"}
        source_values = copy.deepcopy(self._property_values(self.task))
        source_attachment = self._attachments(self.task)

        task_copy = self.task.copy()

        self.assertEqual(self._property_values(self.task), source_values)
        self.assertMovedTo(task_copy.task_properties["notes_html"], notes, [(RED, "image-1.png")], task_copy)
        self.assertEqual(task_copy.task_properties["label"], "Kept")
        copy_values = self._property_values(task_copy)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self._property_values(task_copy), copy_values, "the stored value is the moved one")
        self.assertEqual(self._property_values(self.task), source_values)
        copied_attachment = self._attachments(task_copy)
        self.assertNotEqual(copied_attachment.access_token, source_attachment.access_token)
        self.task.unlink()
        self.assertTrue(copied_attachment.exists())

    def test_moving_texts_saved_before_covers_the_html_properties(self):
        self._define_properties()
        notes = image_html(image_tag(data_uri(RED)))
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET task_properties = %s WHERE id = %s",
            [json.dumps({"notes_html": notes, "label": "Kept"}), self.task.id],
        )
        self.task.invalidate_recordset(["task_properties"])

        result = self.task._move_inline_images_to_attachments()

        self.assertEqual((result["records"], result["images"]), (1, 1))
        self.assertEqual(set(result["texts"][self.task]["task_properties"]), {"notes_html", "label"})
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertMovedTo(self.task.task_properties["notes_html"], notes, [(RED, "image-1.png")], self.task)
        self.assertEqual(self.task.task_properties["label"], "Kept")
