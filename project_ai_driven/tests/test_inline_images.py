import base64
import io
import re
import struct
import time
import zlib
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import psycopg2
from PIL import Image

from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import BaseCase, tagged

from ..models.html_markdown import get_base_host, get_local_url
from ..models.project_task_image_mixin import (
    DRY_RUN_SOURCE,
    ImageMover,
    get_access_token,
    get_file_link,
    get_image_source,
    read_srcset_urls,
    read_style_urls,
    replace_image_sources,
    replace_urls,
    replace_value_urls,
)
from .common import ProjectAiDrivenCommon

# 1x1 PNG images of four colours
RED = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
BLUE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
GREEN = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaGAAAAEEAIFw9selAAAAAElFTkSuQmCC"
YELLOW = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4/58BAAT/Af9dfQKHAAAAAElFTkSuQmCC"
SVG = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="
MARKUP = base64.b64encode(b"<html>").decode()
SOURCE_PATTERN = re.compile(r'src="(/web/image/(\d+)\?access_token=([\w-]+))"')
WRITTEN_BEFORE = datetime(2001, 2, 3, 4, 5, 6)


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


def large_png(compress_level=6):
    """A base64 PNG wider than the 1920 px core resizes an image to."""
    buffer = io.BytesIO()
    Image.new("RGB", (2400, 30), (200, 10, 10)).save(buffer, "PNG", compress_level=compress_level)
    return base64.b64encode(buffer.getvalue()).decode()


def file_link(attachment):
    """The link the 16.0 editor gives an uploaded file, escaped as stored."""
    return "/web/content/%s?unique=%s&amp;download=true&amp;access_token=%s" % (
        attachment.id,
        attachment.checksum,
        attachment.access_token,
    )


def links_html(*hrefs):
    return "".join('<p><a href="%s" title="spec.pdf">spec.pdf</a></p>' % href for href in hrefs)


class InlineImagesCase(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # large_png() and the tests of a resized image assume core's default size
        cls.env["ir.config_parameter"].set_param("base.image_autoresize_max_px", "1920x1920")
        cls.user_reader = cls._create_user("Reader", "project_ai_driven_reader", "reader@example.com")
        cls.other_project = cls.env["project.project"].create({"name": "Not AI-driven"})

    def _attachments(self, task):
        return self.env["ir.attachment"].search([("res_model", "=", "project.task"), ("res_id", "=", task.id)])

    def _get_sources(self, text):
        """[(src, attachment)] of the attachment images of text, in order."""
        return [
            (source, self.env["ir.attachment"].browse(int(attachment_id)))
            for source, attachment_id, _token in SOURCE_PATTERN.findall(text or "")
        ]

    def assertMovedTo(self, text, html, images, task):
        """text is html with each inline image of images (data: attachment name)
        replaced by the link of an attachment of task holding it, and nothing
        else changed.
        """
        self.assertNotIn("data:image", text)
        expected_text = html
        for data, name in images:
            attachment = self._attachments(task).filtered(lambda record, data=data: record.datas == data.encode())
            self.assertEqual(len(attachment), 1, "one attachment of the task per image")
            self.assertEqual(attachment.name, name)
            self.assertEqual(attachment.mimetype, "image/png")
            self.assertTrue(attachment.access_token)
            expected_text = expected_text.replace(
                data_uri(data), "/web/image/%s?access_token=%s" % (attachment.id, attachment.access_token)
            )
        self.assertEqual(text, expected_text)

    def _store_as_before(self, record, fname, text):
        """Store text as a save before this version did: with its inline images."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE %s SET %s = %%s WHERE id = %%s" % (record._table, fname), [text, record.id])
        record.invalidate_recordset([fname])

    def _sanitized(self, model, fname, html):
        """html as the field stores it: the shapes its sanitizer keeps."""
        return str(self.env[model]._fields[fname].convert_to_cache(html, self.env[model]))


@tagged("post_install", "-at_install")
class TestInlineImages(InlineImagesCase):
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
        ):
            yield calls

    def test_create_moves_inline_images_of_description_and_solution(self):
        description = image_html(image_tag(data_uri(RED), ' style="width: 50%;"'))
        solution = image_html(image_tag(data_uri(BLUE)))
        task = self.env["project.task"].create(
            {"name": "Pasted", "project_id": self.project.id, "description": description, "solution": solution}
        )
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
        # the sanitizer of solution drops data-filename and keeps the editor's data-file-name
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
            image_tag(data_uri(MARKUP)),
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
        # not assertRaises: its savepoint flushes, which runs the hook before the block
        with self.assertRaisesRegex(UserError, "Refused on commit"):
            self.env.cr.precommit.run()

    def test_save_moving_an_image_is_tracked_with_its_transaction(self):
        self._flush_tracking()
        messages_before = self.task.message_ids
        self.task.write({"name": "Renamed", "description": image_html(image_tag(data_uri(RED)))})
        self.task.resolution = "data"
        self._flush_tracking()
        tracking_messages = (self.task.message_ids - messages_before).filtered("tracking_value_ids")
        self.assertEqual(len(tracking_messages), 1, "one tracking message for the transaction")
        self.assertEqual(set(tracking_messages.tracking_value_ids.field.mapped("name")), {"name", "resolution"})

    def assertStaysInline(self, source):
        """A save of an inline image core fails to read passes and leaves the
        image inline, on write and on create.
        """
        description = image_html(image_tag(source))
        self.task.description = description
        task = self.env["project.task"].create(
            {"name": "Pasted", "project_id": self.project.id, "description": description}
        )
        for record in (self.task, task):
            self.assertEqual(record.description, description, "the image stays inline")
            self.assertFalse(self._attachments(record))

    def test_inline_image_of_markup_stays(self):
        self.assertStaysInline(data_uri(MARKUP))

    def test_inline_image_too_large_to_open_stays(self):
        self.assertStaysInline(data_uri(png_data(20000, 20000)))

    def test_truncated_inline_image_stays(self):
        # core loads a truncated png (LOAD_TRUNCATED_IMAGES), not a tiff: libtiff's error is raised
        strip = zlib.compress(bytes(3000 * 3000))[:50]
        self.assertStaysInline(data_uri(tiff_data(3000, 3000, strip), "image/tiff"))

    def test_upper_case_and_escaped_schemes_are_moved(self):
        self.task.description = image_html(
            image_tag("DATA:IMAGE/PNG;BASE64,%s" % RED), image_tag("data&#58;image/png;base64,%s" % BLUE)
        )
        sources = self._get_sources(self.task.description)
        self.assertEqual(self.task.description, image_html(*(image_tag(source) for source, _attachment in sources)))
        self.assertEqual([attachment.datas for _source, attachment in sources], [RED.encode(), BLUE.encode()])
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

    def test_lines_give_their_images_to_their_task(self):
        answer = image_html(image_tag(data_uri(RED)))
        expected_results = image_html(image_tag(data_uri(BLUE)))
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "question": "<p>Which screen?</p>", "answer": answer}
        )
        testcase = self.env["project.task.testcase"].create({"task_id": self.task.id, "name": "Shows it"})
        testcase.expected_results = expected_results
        self.assertMovedTo(question.answer, answer, [(RED, "image-1.png")], self.task)
        self.assertMovedTo(testcase.expected_results, expected_results, [(BLUE, "image-1.png")], self.task)
        line_domain = [
            "|",
            "&",
            ("res_model", "=", question._name),
            ("res_id", "=", question.id),
            "&",
            ("res_model", "=", testcase._name),
            ("res_id", "=", testcase.id),
        ]
        self.assertFalse(self.env["ir.attachment"].search(line_domain), "no attachment of a line")

    def test_lines_created_with_the_task(self):
        steps = image_html(image_tag(data_uri(RED)))
        task = self.env["project.task"].create(
            {
                "name": "With a case",
                "project_id": self.project.id,
                "testcase_ids": [Command.create({"name": "Shows it", "steps_to_execute": steps})],
            }
        )
        self.assertMovedTo(task.testcase_ids.steps_to_execute, steps, [(RED, "image-1.png")], task)

    def test_project_user_gets_the_attachment(self):
        description = image_html(image_tag(data_uri(RED)))
        answer = image_html(image_tag(data_uri(BLUE)))
        task = self.task.with_user(self.user_dev)
        task.description = description
        self.env["project.task.question"].with_user(self.user_dev).create({"task_id": task.id, "answer": answer})
        self.assertMovedTo(task.description, description, [(RED, "image-1.png")], self.task)
        self.assertMovedTo(task.question_ids.answer, answer, [(BLUE, "image-1.png")], self.task)
        self.assertEqual(self._attachments(self.task).mapped("create_uid"), self.user_dev)

    def test_reader_of_a_task_cannot_attach_through_it(self):
        self.task.with_user(self.user_reader).read(["name"])
        html = image_html(image_tag(data_uri(RED)))
        with self._count_attachment_calls() as calls:
            with self.assertRaises(AccessError):
                self.task.with_user(self.user_reader).write({"description": html})
            with self.assertRaises(AccessError):
                self.env["project.task.question"].with_user(self.user_reader).create(
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
        self.env["project.task.question"].create({"task_id": self.task.id, "answer": answer})
        self.env["project.task.testcase"].create(
            {"task_id": self.task.id, "name": "Shows it", "expected_results": expected_results}
        )
        source_description = self.task.description

        task_copy = self.task.copy()

        self.assertEqual(self.task.description, source_description)
        self.assertMovedTo(task_copy.description, description, [(RED, "Diagram")], task_copy)
        self.assertMovedTo(task_copy.question_ids.answer, answer, [(BLUE, "image-1.png")], task_copy)
        self.assertMovedTo(
            task_copy.testcase_ids.expected_results, expected_results, [(GREEN, "image-1.png")], task_copy
        )
        copied_attachments = self._attachments(task_copy)
        self.assertEqual(len(copied_attachments), 3)
        self.assertNotEqual(
            set(copied_attachments.mapped("access_token")), set(self._attachments(self.task).mapped("access_token"))
        )
        self.task.unlink()
        self.assertEqual(copied_attachments.exists(), copied_attachments)
        self.assertEqual(set(copied_attachments.mapped("datas")), {RED.encode(), BLUE.encode(), GREEN.encode()})

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
        self.assertEqual(readable_source[1].datas, BLUE.encode())
        self.assertEqual(token_source[1].datas, RED.encode())
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
                self.assertEqual(task_attachment.datas, RED.encode())
                self.assertEqual(
                    task.description,
                    image_html(
                        *foreign_links,
                        image_tag("/web/image/%s?access_token=%s" % (task_attachment.id, task_attachment.access_token)),
                    ),
                    "only the link with the attachment's own token is copied",
                )

    def test_line_moved_to_another_task_gets_copies(self):
        answer = image_html(image_tag(data_uri(RED)))
        steps = image_html(image_tag(data_uri(BLUE)))
        question = self.env["project.task.question"].create({"task_id": self.task.id, "answer": answer})
        testcase = self.env["project.task.testcase"].create(
            {"task_id": self.task.id, "name": "Shows it", "steps_to_execute": steps}
        )
        other_task = self.env["project.task"].create({"name": "Other", "project_id": self.project.id})

        question.with_user(self.user_dev).task_id = other_task
        testcase.task_id = other_task

        self.assertMovedTo(question.answer, answer, [(RED, "image-1.png")], other_task)
        self.assertMovedTo(testcase.steps_to_execute, steps, [(BLUE, "image-1.png")], other_task)
        copied_attachments = self._attachments(other_task)
        self.task.unlink()
        self.assertEqual(len(copied_attachments.exists()), 2)

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
        self.assertFalse(self._attachments(self.task))

    def test_save_without_html_fields_does_not_look_for_them(self):
        with patch.object(type(self.task), "_get_image_fields", return_value=[]) as get_image_fields:
            self.task.write({"name": "Renamed", "priority": "1"})
        get_image_fields.assert_not_called()

    def test_write_naming_a_key_core_drops_still_passes(self):
        self.assertTrue(self.env["project.task"].browse().write({"x_not_a_field": 1}))
        self.assertTrue(self.task.write({"parent_path": "1/", "name": "Renamed"}))
        self.assertEqual(self.task.name, "Renamed")

    def test_recurrence_gives_each_task_its_own_attachments(self):
        self.project.allow_recurring_tasks = True
        description = image_html(image_tag(data_uri(RED)))
        task = self.env["project.task"].create(
            {
                "name": "Every Monday",
                "project_id": self.project.id,
                "description": description,
                "recurring_task": True,
                "repeat_interval": 1,
                "repeat_unit": "week",
                "mon": True,
                "repeat_type": "forever",
            }
        )
        task.recurrence_id._create_next_task()
        next_task = task.recurrence_id.task_ids - task
        self.assertEqual(len(next_task), 1)
        self.assertMovedTo(next_task.description, description, [(RED, "image-1.png")], next_task)

        new_description = image_html(image_tag(data_uri(BLUE)))
        task.write({"description": new_description, "recurrence_update": "all"})
        for recurring_task in task | next_task:
            self.assertMovedTo(recurring_task.description, new_description, [(BLUE, "image-1.png")], recurring_task)

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
        tasks = self.task | self.env["project.task"].create({"name": "Second", "project_id": self.project.id})
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
        self.assertFalse(self._attachments(self.task))
        self.assertEqual(self.task.description, description)

        result = self.task._move_inline_images_to_attachments(store=False)

        new_texts = result.pop("texts")
        self.assertEqual(result, {"records": 2, "images": 2, "bytes": image_bytes})
        self.assertEqual(set(new_texts), {self.task, question})
        self.assertMovedTo(new_texts[self.task]["description"], description, [(RED, "image-1.png")], self.task)
        self.assertMovedTo(new_texts[question]["answer"], answer, [(BLUE, "image-2.png")], self.task)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self.task.description, description, "store=False leaves the text as it is")
        self.assertEqual(question.answer, answer)
        for record in (self.task, question):
            self.env.cr.execute("SELECT write_date FROM %s WHERE id = %%s" % record._table, [record.id])
            self.assertEqual(self.env.cr.fetchone()[0], WRITTEN_BEFORE, "store=False writes nothing on the record")

    def test_moving_texts_saved_before_counts_only_images_moved(self):
        description = image_html(image_tag(data_uri(MARKUP)), image_tag(data_uri(RED)))
        self._store_as_before(self.task, "description", description)
        result = self.task._move_inline_images_to_attachments(store=False)
        self.assertEqual(
            (result["images"], result["bytes"], len(self._attachments(self.task))),
            (1, len(base64.b64decode(RED)), 1),
            "one attachment per image counted",
        )
        new_text = result["texts"][self.task]["description"]
        [(source, attachment)] = self._get_sources(new_text)
        self.assertEqual(new_text, description.replace(data_uri(RED), source), "the markup image stays inline")
        self.assertEqual(attachment.datas, RED.encode())

    def test_dry_run_counts_as_the_run(self):
        description = image_html(
            image_tag(data_uri(MARKUP)), image_tag(data_uri(RED)), image_tag(data_uri(png_data(20000, 20000)))
        )
        strip = zlib.compress(bytes(3000 * 3000))[:50]
        answer = image_html(image_tag(data_uri(tiff_data(3000, 3000, strip), "image/tiff")))
        question = self.env["project.task.question"].create({"task_id": self.task.id})
        self._store_as_before(self.task, "description", description)
        self._store_as_before(question, "answer", answer)
        expected_counts = {"records": 1, "images": 1, "bytes": len(base64.b64decode(RED))}

        dry_run = self.task._move_inline_images_to_attachments(dry_run=True)
        result = self.task._move_inline_images_to_attachments(store=False)

        self.assertEqual(dry_run, dict(expected_counts, texts={}))
        self.assertEqual(set(result.pop("texts")), {self.task})
        self.assertEqual(result, expected_counts)

    def test_dry_run_copies_no_link(self):
        other_task = self.env["project.task"].create(
            {"name": "Other", "project_id": self.other_project.id, "description": image_html(image_tag(data_uri(RED)))}
        )
        [(source, _image)] = self._get_sources(other_task.description)
        spec = self.env["ir.attachment"].create(
            {"name": "spec.pdf", "raw": b"%PDF-1.4 spec", "res_model": "project.task", "res_id": other_task.id}
        )
        attachment_count = self.env["ir.attachment"].search_count([])
        mover = ImageMover(self.env(user=self.user_dev.id), dry_run=True, copy_links=True)

        new_text = mover.move(image_html(image_tag(source)) + links_html("/web/content/%s" % spec.id), self.task)

        self.assertEqual(new_text, image_html(image_tag(DRY_RUN_SOURCE)) + links_html(DRY_RUN_SOURCE))
        self.assertEqual(self.env["ir.attachment"].search_count([]), attachment_count)

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
        with self._count_attachment_calls() as calls:
            self.task.write({"description": "<p>Plain text</p>", "solution": "<p>See /web/image/1</p>"})
            self.env["project.task.question"].create({"task_id": self.task.id, "answer": "<p>Plain answer</p>"})
            self.env.flush_all()
            calls_without_images = list(calls)
            self.task.solution = image_html(image_tag(data_uri(RED)))
        self.assertEqual(calls_without_images, [])
        self.assertIn("create", calls, "a save with an image is counted")


@tagged("post_install", "-at_install")
class TestImageReuse(InlineImagesCase):
    def test_same_image_saved_again_keeps_its_attachment(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        [(source, _attachment)] = self._get_sources(self.task.description)
        self.task.write(
            {
                "description": image_html(image_tag(data_uri(RED)), image_tag(data_uri(BLUE))),
                "solution": image_html(image_tag(data_uri(RED))),
            }
        )
        self.assertEqual(self._get_sources(self.task.description)[0][0], source)
        self.assertEqual(self.task.solution, image_html(image_tag(source)))
        self.assertEqual(len(self._attachments(self.task)), 2, "a new attachment for the new image only")

    def test_task_and_its_rows_share_an_attachment(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": image_html(image_tag(data_uri(RED)))}
        )
        testcase = self.env["project.task.testcase"].create(
            {"task_id": self.task.id, "name": "Shows it", "steps_to_execute": image_html(image_tag(data_uri(RED)))}
        )
        [(source, _attachment)] = self._get_sources(self.task.description)
        self.assertEqual(question.answer, image_html(image_tag(source)))
        self.assertEqual(testcase.steps_to_execute, image_html(image_tag(source)))
        self.assertEqual(len(self._attachments(self.task)), 1)

        task = self.env["project.task"].create(
            {
                "name": "Created with a row",
                "project_id": self.project.id,
                "description": image_html(image_tag(data_uri(BLUE))),
                "question_ids": [Command.create({"answer": image_html(image_tag(data_uri(BLUE)))})],
            }
        )
        self.assertEqual(task.question_ids.answer, task.description)
        self.assertEqual(len(self._attachments(task)), 1)

    def test_another_task_gets_its_own_attachment(self):
        description = image_html(image_tag(data_uri(RED)))
        self.task.description = description
        other_task = self.env["project.task"].create(
            {"name": "Other", "project_id": self.project.id, "description": description}
        )
        self.assertMovedTo(other_task.description, description, [(RED, "image-1.png")], other_task)
        self.assertNotEqual(other_task.description, self.task.description)

    def test_attachment_the_task_has_is_used_with_a_new_token(self):
        attachment = self.env["ir.attachment"].create(
            {"name": "red.png", "datas": RED, "res_model": "project.task", "res_id": self.task.id}
        )
        self.assertFalse(attachment.access_token)
        self.task.description = image_html(image_tag(data_uri(RED)))
        self.assertTrue(attachment.access_token)
        self.assertEqual(self.task.description, image_html(image_tag(get_image_source(attachment))))
        self.assertEqual(self._attachments(self.task), attachment)

    def test_attachment_of_a_message_is_not_used(self):
        upload = self.env["ir.attachment"].create(
            {"name": "red.png", "datas": RED, "res_model": "mail.compose.message", "res_id": 0}
        )
        note = self.task.message_post(
            body="Screenshot", attachment_ids=upload.ids, message_type="comment", subtype_xmlid="mail.mt_note"
        )
        self.assertEqual((upload.res_model, upload.res_id), ("project.task", self.task.id))

        self.task.description = image_html(image_tag(data_uri(RED)))

        [(_source, image)] = self._get_sources(self.task.description)
        self.assertNotEqual(image, upload, "the image of a note goes with the note")
        self.task._message_update_content(note, "", attachment_ids=[])
        self.assertFalse(upload.exists())
        self.assertEqual(image.exists().datas, RED.encode(), "the description keeps its image")

    def test_value_of_a_binary_field_is_not_used(self):
        field_value = self.env["ir.attachment"].create(
            {"name": "photo", "datas": RED, "res_model": "project.task", "res_id": self.task.id, "res_field": "photo"}
        )
        self.task.description = image_html(image_tag(data_uri(RED)))
        [(_source, image)] = self._get_sources(self.task.description)
        self.assertNotEqual(image, field_value)
        self.assertEqual(image.datas, RED.encode())

    def test_attachment_its_saver_cannot_read_is_not_used(self):
        upload = self.env["ir.attachment"].create(
            {"name": "red.png", "datas": RED, "res_model": "project.task", "res_id": self.task.id}
        )
        portal_user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Portal",
                    "login": "project_ai_driven_portal",
                    "groups_id": [Command.set(self.env.ref("base.group_portal").ids)],
                }
            )
        )
        text = image_html(image_tag(data_uri(RED)))

        [(_source, image)] = self._get_sources(ImageMover(self.env(user=portal_user.id)).move(text, self.task))
        [(_source, sudo_image)] = self._get_sources(
            ImageMover(self.env(user=portal_user.id, su=True)).move(text, self.task)
        )

        self.assertNotEqual(image, upload, "no token or name of an attachment its saver cannot read")
        self.assertEqual(image.datas, RED.encode())
        # core saves a portal user's task in sudo: the rights checked are still the user's own
        self.assertNotEqual(sudo_image, upload, "sudo does not hand them the attachment either")
        self.assertEqual(sudo_image.datas, RED.encode())
        [(_source, own_image)] = self._get_sources(
            ImageMover(self.env(user=self.user_lead.id, su=True)).move(text, self.task)
        )
        self.assertEqual(own_image, upload, "a saver who may read it, in sudo or not, uses it")

    def test_dry_run_gives_no_token_to_an_attachment_it_would_use(self):
        attachment = self.env["ir.attachment"].create(
            {"name": "red.png", "datas": RED, "res_model": "project.task", "res_id": self.task.id}
        )
        self._store_as_before(self.task, "description", image_html(image_tag(data_uri(RED))))
        dry_run = self.task._move_inline_images_to_attachments(dry_run=True)
        self.assertEqual((dry_run["records"], dry_run["images"]), (1, 0), "the run would use the attachment")
        self.assertFalse(attachment.access_token)

    def test_attachment_of_another_type_is_not_used(self):
        svg_attachment = self.env["ir.attachment"].create(
            {"name": "logo.svg", "datas": SVG, "res_model": "project.task", "res_id": self.task.id}
        )
        self.assertEqual(svg_attachment.mimetype, "image/svg+xml")
        self._store_as_before(self.task, "description", image_html(image_tag(data_uri(SVG, "image/svg+xml"))))
        self.task.with_user(self.user_dev)._move_inline_images_to_attachments()
        text_attachment = self._attachments(self.task) - svg_attachment
        self.assertEqual(text_attachment.mimetype, "text/plain", "a user who may not edit views gets svg as text")
        self.assertEqual(text_attachment.checksum, svg_attachment.checksum)

    def test_resized_image_pasted_again_keeps_its_attachment(self):
        image = large_png()
        self.task.description = image_html(image_tag(data_uri(image)))
        [(source, attachment)] = self._get_sources(self.task.description)
        self.assertEqual(Image.open(io.BytesIO(attachment.raw)).size, (1920, 24), "core resized the image")
        self.task.solution = image_html(image_tag(data_uri(image)))
        self.assertEqual(self.task.solution, image_html(image_tag(source)))
        self.assertEqual(self._attachments(self.task), attachment)

    def test_dry_run_counts_the_attachments_the_run_creates(self):
        self.task.description = image_html(image_tag(data_uri(RED)))
        question = self.env["project.task.question"].create({"task_id": self.task.id})
        # one image in two encodings: different data, the same attachment once resized
        fast_image, small_image = large_png(compress_level=1), large_png(compress_level=9)
        self.assertNotEqual(fast_image, small_image)
        self._store_as_before(
            self.task, "solution", image_html(image_tag(data_uri(RED)), image_tag(data_uri(fast_image)))
        )
        self._store_as_before(
            question, "answer", image_html(image_tag(data_uri(small_image)), image_tag(data_uri(BLUE)))
        )
        expected_counts = {
            "records": 2,
            "images": 2,
            "bytes": len(base64.b64decode(fast_image)) + len(base64.b64decode(BLUE)),
        }

        dry_run = self.task._move_inline_images_to_attachments(dry_run=True)
        result = self.task._move_inline_images_to_attachments(store=False)

        self.assertEqual(dry_run, dict(expected_counts, texts={}))
        new_texts = result.pop("texts")
        self.assertEqual(result, expected_counts)
        self.assertEqual(len(self._attachments(self.task)), 3)
        [(red_source, _red), (large_source, _large)] = self._get_sources(new_texts[self.task]["solution"])
        self.assertEqual(red_source, self._get_sources(self.task.description)[0][0])
        self.assertEqual(self._get_sources(new_texts[question]["answer"])[0][0], large_source)


@tagged("post_install", "-at_install")
class TestAbsoluteImageLinks(InlineImagesCase):
    def test_link_by_the_host_of_this_database_is_copied(self):
        self.env["ir.config_parameter"].set_param("web.base.url", "https://odoo.example.com")
        other_task = self.env["project.task"].create(
            {"name": "Other", "project_id": self.other_project.id, "description": image_html(image_tag(data_uri(RED)))}
        )
        [(source, _attachment)] = self._get_sources(other_task.description)
        local_links = (
            "https://ODOO.example.com" + source,
            "https://odoo.example.com:443" + source,
            "//odoo.example.com" + source,
            "http://odoo.example.com" + source,
            "http://odoo.example.com:80" + source,
            "https://evil@odoo.example.com" + source,
        )
        foreign_links = (
            "https://other.example" + source,
            "https://odoo.example.com:8443" + source,
            "http://odoo.example.com:443" + source,
            "https://odoo.example.com.other.example" + source,
            "https://odoo.example.com@evil.example" + source,
        )

        task = self.env["project.task"].create(
            {
                "name": "Pasted from a mail",
                "project_id": self.project.id,
                "description": image_html(*(image_tag(link) for link in local_links + foreign_links)),
            }
        )

        [image_copy] = self._attachments(task)
        self.assertEqual(image_copy.datas, RED.encode())
        self.assertEqual(
            task.description,
            image_html(
                *(image_tag(get_image_source(image_copy)) for _link in local_links),
                *(image_tag(link) for link in foreign_links),
            ),
        )

    def test_without_web_base_url_only_relative_links_are_copied(self):
        self.env["ir.config_parameter"].set_param("web.base.url", "")
        other_task = self.env["project.task"].create(
            {"name": "Other", "project_id": self.other_project.id, "description": image_html(image_tag(data_uri(RED)))}
        )
        [(source, _attachment)] = self._get_sources(other_task.description)
        absolute_links = ("https://odoo.example.com" + source, "//odoo.example.com" + source)

        task = self.env["project.task"].create(
            {
                "name": "Pasted from a mail",
                "project_id": self.project.id,
                "description": image_html(*(image_tag(link) for link in absolute_links), image_tag(source)),
            }
        )

        [image_copy] = self._attachments(task)
        self.assertEqual(
            task.description,
            image_html(*(image_tag(link) for link in absolute_links), image_tag(get_image_source(image_copy))),
        )


@tagged("post_install", "-at_install")
class TestSrcsetAndStyleImages(InlineImagesCase):
    def test_srcset_and_style_images_of_the_description_are_moved(self):
        html = (
            '<p><source srcset="%s 480w, https://example.com/wide.png 800w" media="(min-width: 600px)"></p>'
            '<p><img src="%s" srcset="%s 1x,%s 2x" alt="Shot"></p>'
            "<div style=\"font-family: 'Noto Sans'; background-image: url(&quot;%s&quot;)\">Quoted</div>"
            "<p style=\"background: url('%s') no-repeat\">Single</p>"
            '<p style="background-image:url(%s)">Bare</p>'
        ) % (
            data_uri(YELLOW),
            data_uri(RED),
            data_uri(BLUE),
            data_uri(GREEN),
            data_uri(RED),
            data_uri(BLUE),
            data_uri(GREEN),
        )
        description = self._sanitized("project.task", "description", html)
        self.assertEqual(description.count("srcset"), 2, "the description keeps srcset")
        self.task.description = html
        self.assertMovedTo(
            self.task.description,
            description,
            [(YELLOW, "image-1.png"), (RED, "Shot"), (BLUE, "Shot"), (GREEN, "Shot")],
            self.task,
        )

    def test_style_images_of_a_row_are_moved_to_its_task(self):
        html = "<p style=\"background-image: url('%s')\">Answer</p>" % data_uri(RED)
        answer = self._sanitized("project.task.question", "answer", html)
        question = self.env["project.task.question"].create({"task_id": self.task.id, "answer": html})
        self.assertMovedTo(question.answer, answer, [(RED, "image-1.png")], self.task)

    def test_new_task_copies_srcset_and_style_images_of_another_task(self):
        self.env["ir.config_parameter"].set_param("web.base.url", "https://odoo.example.com")
        other_task = self.env["project.task"].create(
            {
                "name": "Other",
                "project_id": self.other_project.id,
                "description": image_html(image_tag(data_uri(RED)), image_tag(data_uri(BLUE))),
            }
        )
        [(red_source, _red), (blue_source, _blue)] = self._get_sources(other_task.description)
        html = (
            '<p><img srcset="%s 1x, https://odoo.example.com%s 2x"></p>'
            "<div style=\"font-family: 'Noto Sans'; background-image: url(&quot;%s&quot;)\">Quoted</div>"
        ) % (red_source, blue_source, red_source)

        task = self.env["project.task"].create({"name": "Copied", "project_id": self.project.id, "description": html})

        copies = self._attachments(task)
        self.assertEqual(len(copies), 2)
        red_copy = copies.filtered(lambda attachment: attachment.datas == RED.encode())
        blue_copy = copies - red_copy
        expected_html = html.replace("https://odoo.example.com" + blue_source, get_image_source(blue_copy)).replace(
            red_source, get_image_source(red_copy)
        )
        self.assertEqual(task.description, self._sanitized("project.task", "description", expected_html))


@tagged("post_install", "-at_install")
class TestFileLinks(InlineImagesCase):
    def _attach_file(self, task, raw, name):
        return task.env["ir.attachment"].create(
            {
                "name": name,
                "raw": raw,
                "res_model": "project.task",
                "res_id": task.id,
                "access_token": self.env["ir.attachment"]._generate_access_token(),
            }
        )

    def test_copy_of_a_task_links_its_own_copy_of_a_file(self):
        self.env["ir.config_parameter"].set_param("web.base.url", "https://odoo.example.com")
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        absolute_link = "https://odoo.example.com/web/content/%s/spec.pdf?download=true&amp;access_token=%s"
        self.task.description = links_html(file_link(spec), absolute_link % (spec.id, spec.access_token))
        self.env["project.task.question"].create({"task_id": self.task.id, "answer": links_html(file_link(spec))})

        task_copy = self.task.copy()

        spec_copy = self._attachments(task_copy)
        self.assertEqual(len(spec_copy), 1, "the task and its row share the copy")
        self.assertEqual(
            task_copy.description,
            links_html(
                file_link(spec_copy),
                "/web/content/%s/spec.pdf?download=true&amp;access_token=%s" % (spec_copy.id, spec_copy.access_token),
            ),
        )
        self.assertEqual(task_copy.question_ids.answer, links_html(file_link(spec_copy)))
        self.assertNotEqual(spec_copy.access_token, spec.access_token)
        self.task.unlink()
        self.assertEqual(spec_copy.exists().raw, b"%PDF-1.4 spec")

    def test_file_is_copied_as_uploaded(self):
        plan = base64.b64decode(large_png())
        upload = self._attach_file(self.task.with_context(image_no_postprocess=True), plan, "plan.png")
        self.assertEqual(upload.raw, plan)
        self.task.description = links_html(file_link(upload)) + image_html(image_tag(get_image_source(upload)))
        task_copy = self.task.copy()
        file_copy = self.env["ir.attachment"].browse(int(re.search(r"/web/content/(\d+)", task_copy.description)[1]))
        [(_source, image_copy)] = self._get_sources(task_copy.description)
        self.assertNotEqual(file_copy, upload)
        self.assertEqual((file_copy.res_model, file_copy.res_id), ("project.task", task_copy.id))
        self.assertEqual(file_copy.raw, plan, "a file link gets the file as uploaded")
        self.assertEqual(Image.open(io.BytesIO(image_copy.raw)).size, (1920, 24), "an image shown is resized")

    def test_markup_file_is_text_for_a_user_who_may_not_edit_views(self):
        page = self._attach_file(self.task, b"<html><body>Report</body></html>", "report.html")
        self.assertEqual(page.mimetype, "text/html")
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .create({"name": "Linked", "project_id": self.project.id, "description": links_html(file_link(page))})
        )
        self.assertEqual(self._attachments(task).mimetype, "text/plain")

    def test_file_link_copied_in_sudo_only_with_its_token(self):
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        link_without_token = "/web/content/%s?download=true" % spec.id
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .sudo()
            .create(
                {
                    "name": "Posted by a form",
                    "project_id": self.project.id,
                    "description": links_html(link_without_token, file_link(spec)),
                }
            )
        )
        [spec_copy] = self._attachments(task)
        self.assertEqual(task.description, links_html(link_without_token, file_link(spec_copy)))

    def test_link_whose_token_is_not_ascii_stays(self):
        # in sudo, as a recurrence creates its next task from a text anyone wrote
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        links = (
            "/web/content/%s?download=true&amp;access_token=%%C3%%A9" % spec.id,
            "/web/content/%s?download=true&amp;access_token=%%ff" % spec.id,
        )
        style = "<div style=\"background-image: url('/web/image/%s?access_token=é')\">x</div>" % spec.id
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .sudo()
            .create(
                {"name": "Next occurrence", "project_id": self.project.id, "description": links_html(*links) + style}
            )
        )
        self.assertFalse(self._attachments(task), "no token: nothing copied")
        self.assertEqual(task.description, links_html(*links) + style)

    def test_attachment_whose_token_is_not_ascii_is_not_copied(self):
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        spec.sudo().access_token = "é"
        links = ("/web/content/%s?download=true&amp;access_token=abc" % spec.id,)
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .sudo()
            .create({"name": "Next occurrence", "project_id": self.project.id, "description": links_html(*links)})
        )
        self.assertFalse(self._attachments(task), "its token matches nothing: nothing copied")
        self.assertEqual(task.description, links_html(*links))

    def test_link_whose_id_names_no_attachment_stays(self):
        links = ("/web/content/%s?download=true" % ("9" * 5000), "/web/content/%s" % ("9" * 10))
        image = '<p><img src="/web/image/%s"></p>' % ("9" * 5000)
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .sudo()
            .create(
                {"name": "Next occurrence", "project_id": self.project.id, "description": links_html(*links) + image}
            )
        )
        self.assertFalse(self._attachments(task), "no attachment of another task: nothing copied")
        self.assertEqual(task.description, links_html(*links) + image)

    def test_file_of_a_task_its_user_cannot_read_is_not_copied(self):
        hidden_project = self.env["project.project"].create({"name": "Hidden", "privacy_visibility": "followers"})
        hidden_task = self.env["project.task"].create({"name": "Hidden", "project_id": hidden_project.id})
        hidden_file = self._attach_file(hidden_task, b"%PDF-1.4 hidden", "hidden.pdf")
        description = links_html("/web/content/%s?download=true" % hidden_file.id)
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .create({"name": "Pasted", "project_id": self.project.id, "description": description})
        )
        self.assertEqual(task.description, description)
        self.assertFalse(self._attachments(task))

    def test_write_of_a_file_link_to_another_task_keeps_it(self):
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        other_task = self.env["project.task"].create({"name": "Other", "project_id": self.project.id})
        other_task.description = links_html(file_link(spec))
        self.assertEqual(other_task.description, links_html(file_link(spec)))
        self.assertFalse(self._attachments(other_task))

    def test_file_link_to_no_other_task_stays(self):
        partner_card = self.env["ir.attachment"].create(
            {
                "name": "card.pdf",
                "raw": b"%PDF-1.4 card",
                "res_model": "res.partner",
                "res_id": self.user_dev.partner_id.id,
                "access_token": self.env["ir.attachment"]._generate_access_token(),
            }
        )
        loose_file = self.env["ir.attachment"].create(
            {
                "name": "loose.pdf",
                "raw": b"%PDF-1.4 loose",
                "res_model": "project.task",
                "access_token": self.env["ir.attachment"]._generate_access_token(),
            }
        )
        own_file = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        description = links_html(file_link(partner_card), file_link(loose_file))

        task = self.env["project.task"].create(
            {"name": "Linked", "project_id": self.project.id, "description": description}
        )
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": links_html(file_link(own_file))}
        )

        self.assertEqual(task.description, description)
        self.assertFalse(self._attachments(task))
        self.assertEqual(question.answer, links_html(file_link(own_file)), "a link to its own task's file stays")
        self.assertEqual(self._attachments(self.task), own_file)

    def test_copied_file_link_keeps_its_other_parameters_and_fragment(self):
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        link = "/web/content/%s/spec.pdf?download=true&amp;name=a%%20b&amp;access%%5Ftoken=%s#page=2"
        task = (
            self.env["project.task"]
            .with_user(self.user_dev)
            .create(
                {
                    "name": "Linked",
                    "project_id": self.project.id,
                    "description": links_html(link % (spec.id, spec.access_token)),
                }
            )
        )
        [spec_copy] = self._attachments(task)
        expected_link = "/web/content/%s/spec.pdf?download=true&amp;name=a%%20b&amp;access_token=%s#page=2"
        self.assertEqual(task.description, links_html(expected_link % (spec_copy.id, spec_copy.access_token)))

    def test_row_moved_to_another_task_copies_its_file_links(self):
        spec = self._attach_file(self.task, b"%PDF-1.4 spec", "spec.pdf")
        question = self.env["project.task.question"].create(
            {"task_id": self.task.id, "answer": links_html(file_link(spec))}
        )
        other_task = self.env["project.task"].create({"name": "Other", "project_id": self.project.id})
        question.with_user(self.user_dev).task_id = other_task
        [spec_copy] = self._attachments(other_task)
        self.assertEqual(question.answer, links_html(file_link(spec_copy)))
        self.assertEqual(spec_copy.raw, b"%PDF-1.4 spec")


@tagged("post_install", "-at_install")
class TestUrlReaders(BaseCase):
    def test_port_of_this_database_counts_as_none_only_where_it_is_the_default(self):
        cases = {
            ("http://odoo.example.com:8069", "http://odoo.example.com:8069/web/image/5"): "/web/image/5",
            ("http://odoo.example.com:8069", "http://odoo.example.com/web/image/5"): None,
            ("http://odoo.example.com:8069", "https://odoo.example.com:8069/web/image/5"): "/web/image/5",
            ("https://odoo.example.com:443", "http://odoo.example.com/web/image/5"): "/web/image/5",
            ("https://odoo.example.com:443", "http://odoo.example.com:443/web/image/5"): None,
            ("https://odoo.example.com", "//odoo.example.com:443/web/image/5"): "/web/image/5",
        }
        for (base_url, url), local_url in cases.items():
            with self.subTest(base_url=base_url, url=url):
                self.assertEqual(get_local_url(url, get_base_host(base_url)), local_url)

    def test_access_token_read_as_the_server_reads_it(self):
        links = {
            "/web/content/5?download=true&access_token=abc": "abc",
            "/web/content/5?access%5Ftoken=abc&access_token=def": "abc",
            "/web/image/5?access_token=a%2Bb#frag": "a+b",
            "/web/image/5?Access_Token=abc": None,
            "/web/image/5": None,
        }
        for link, token in links.items():
            with self.subTest(link=link):
                self.assertEqual(get_access_token(link), token)

    def test_srcset_urls_split_as_the_html_standard_does(self):
        urls = {
            "a.png 1x, b.png 2x": ["a.png", "b.png"],
            "data:image/png;base64,AA== 1x,data:image/png;base64,BB 2x": [
                "data:image/png;base64,AA==",
                "data:image/png;base64,BB",
            ],
            "a.png,, b.png": ["a.png", "b.png"],
            "a.png (1, 2) 1x, b.png": ["a.png", "b.png"],
            "a,b.png 480w": ["a,b.png"],
            " ,\n": [],
        }
        for value, expected_urls in urls.items():
            with self.subTest(value=value):
                self.assertEqual([value[start:end] for start, end in read_srcset_urls(value)], expected_urls)

    def test_style_urls_quoted_or_not(self):
        value = "background: url(a.png), URL( \"b.png\" ), url('c d.png'); content: 'url'"
        self.assertEqual([value[start:end] for start, end in read_style_urls(value)], ["a.png", "b.png", "c d.png"])

    def test_new_url_is_escaped_and_the_rest_kept(self):
        new_urls = {"/old?a=1&b=2": "/new?c=3&d=4"}
        style = "font-family: &quot;Noto Sans&quot;; background-image: url(&quot;/old?a=1&amp;b=2&quot;) no-repeat"
        srcset = "/old?a=1&amp;b=2 1x,&#10;/other 2x"
        self.assertEqual(
            replace_value_urls(style, read_style_urls, new_urls.get),
            "font-family: &quot;Noto Sans&quot;; background-image: url(&quot;/new?c=3&amp;d=4&quot;) no-repeat",
        )
        self.assertEqual(
            replace_value_urls(srcset, read_srcset_urls, new_urls.get), "/new?c=3&amp;d=4 1x,&#10;/other 2x"
        )

    def test_image_sources_alone_are_replaced_for_former_callers(self):
        text = '<p style="background: url(a.png)"><img src="a.png" srcset="a.png 2x" alt="A &amp; B"></p>'
        calls = []

        def get_new_source(source, attributes):
            calls.append((source, attributes))
            return "/b.png?x=1&y=2"

        self.assertEqual(
            replace_image_sources(text, get_new_source), text.replace('src="a.png"', 'src="/b.png?x=1&amp;y=2"')
        )
        self.assertEqual(calls, [("a.png", {"src": "a.png", "srcset": "a.png 2x", "alt": "A & B"})])

    def test_tags_of_a_hostile_comment_are_read_in_linear_time(self):
        # unclosed tags: names running into the next tag, and quotes that pair in many ways
        for comment in ("<a" * 800 + ' "', '<a" "' * 16000, "<a' '" * 16000, '<a" "<' * 16000):
            with self.subTest(comment=comment[:12]):
                text = '<p><img src="%s"></p><!-- %s -->' % (data_uri(RED), comment)
                urls = []
                started = time.perf_counter()
                replace_urls(text, lambda url, kind, attributes, urls=urls: urls.append(url))
                self.assertLess(time.perf_counter() - started, 1)
                self.assertEqual(urls, [data_uri(RED)])

    def test_style_url_without_its_end_is_read_in_linear_time(self):
        value = "background: url(" + " " * 50000
        started = time.perf_counter()
        self.assertEqual(read_style_urls(value), [])
        self.assertLess(time.perf_counter() - started, 1)
        self.assertEqual(read_style_urls("background: url( ), url()"), [])

    def test_file_link_takes_the_token_of_its_attachment(self):
        attachment = SimpleNamespace(id=9, access_token="new")
        link = "/web/content/5/a.pdf?access%5Ftoken=old&x=%41&access_token=old&y#page=2"
        self.assertEqual(get_file_link(link, attachment), "/web/content/9/a.pdf?x=%41&y&access_token=new#page=2")
