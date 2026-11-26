from ast import literal_eval

from lxml import etree

from odoo.tests.common import TransactionCase, tagged
from odoo.tools.template_inheritance import apply_inheritance_specs

from ..hooks import MODULE
from .common import ProjectAiDrivenCommon

VIEW_TAGS = ("form", "tree", "kanban", "calendar")
STARS = "//field[@name='priority'][@widget='priority']"
TITLE_STAR = "//div[hasclass('oe_title')]//field[@name='priority']"
FORM_STAR_ANCHOR = "//sheet//field[@name='priority'][not(ancestor::field)]"
# before the title, where OCA project_task_add_very_high moves the star, and below the inline lists
PLACES_IN_THE_SHEET = (("//div[hasclass('oe_title')]", "addprevious"), ("//sheet/notebook", "addnext"))
# the form shows it in its title and in the lists of sub-tasks and of blocking tasks
STAR_COUNTS = {
    "project.view_task_form2": 3,
    "project.view_task_kanban": 1,
    "project.view_task_tree2": 1,
    "project.view_task_calendar": 1,
}
HIDDEN_ON_FLAGGED_PROJECTS = {"invisible": [("is_ai_driven", "=", True)]}
# computed from the checklist items and the test cases, whose readers are project users
LINE_COUNT_FIELDS = (
    "completion",
    "completion_xml",
    "kanban_checklist",
    "testcase_completion",
    "testcase_completion_xml",
    "kanban_testcases",
)


@tagged("post_install", "-at_install")
class TestTaskViews(TransactionCase):
    def _combined_arch(self, xmlid):
        return self.env.ref(xmlid)._get_combined_arch()

    def _view_node(self, node):
        """Return the view node belongs to: a list inside a form reads its own fields."""
        return next(node.iterancestors(*VIEW_TAGS))

    def test_star_is_hidden_on_tasks_of_flagged_projects(self):
        for xmlid, star_count in STAR_COUNTS.items():
            arch = self._combined_arch(xmlid)
            stars = arch.xpath(STARS)
            self.assertGreaterEqual(len(stars), star_count, xmlid)
            flagged_views = {self._view_node(flag) for flag in arch.xpath("//field[@name='is_ai_driven']")}
            for star in stars:
                self.assertEqual(literal_eval(star.get("attrs") or "{}"), HIDDEN_ON_FLAGGED_PROJECTS, xmlid)
                self.assertIn(self._view_node(star), flagged_views, xmlid)

    def _core_form_arch(self):
        return etree.fromstring(self.env.ref("project.view_task_form2").arch)

    def _own_form_arch(self):
        return etree.fromstring(self.env.ref("%s.view_task_form2_common" % MODULE).arch)

    def test_form_star_anchor_takes_the_one_star_of_the_core_form_outside_its_lists(self):
        self.assertIn(FORM_STAR_ANCHOR, [spec.get("expr") for spec in self._own_form_arch().iter("xpath")])
        core_arch = self._core_form_arch()
        title_stars = core_arch.xpath(TITLE_STAR)
        self.assertEqual(len(title_stars), 1)
        self.assertEqual(core_arch.xpath(FORM_STAR_ANCHOR), title_stars)

    def test_form_star_is_hidden_wherever_another_module_puts_it_in_the_sheet(self):
        for place, move_star_there in PLACES_IN_THE_SHEET:
            core_arch = self._core_form_arch()
            getattr(core_arch.xpath(place)[0], move_star_there)(core_arch.xpath(TITLE_STAR)[0])

            arch = apply_inheritance_specs(core_arch, self._own_form_arch())

            stars = arch.xpath(STARS)
            self.assertEqual(len(stars), STAR_COUNTS["project.view_task_form2"], place)
            for star in stars:
                self.assertEqual(literal_eval(star.get("attrs") or "{}"), HIDDEN_ON_FLAGGED_PROJECTS, place)

    def test_form_shows_the_estimate_the_actual_hours_the_size_and_the_type_together(self):
        arch = self._combined_arch("project.view_task_form2")
        estimate = arch.xpath("//field[@name='priority_level']")[0].getnext()
        actual_hours = estimate.getnext()
        size = actual_hours.getnext()
        self.assertEqual(
            (estimate.get("name"), estimate.get("string"), estimate.get("widget")),
            ("planned_hours", "Estimate", "float_time"),
        )
        self.assertEqual(
            [actual_hours.get("name"), size.get("name"), size.getnext().get("name")],
            ["actual_hours", "tshirt_estimate", "task_type"],
        )
        size_field = self.env["project.task"].fields_get(["tshirt_estimate"])["tshirt_estimate"]
        self.assertEqual((size.get("string"), size_field["string"], size_field["readonly"]), (None, "Size", True))

    def test_estimate_is_editable_wherever_the_form_shows_it(self):
        """The web client saves a field by the modifiers of its last node on the
        form, so one read-only node of the hours would stop the estimate saving.
        """
        arch = self._combined_arch("project.view_task_form2")
        hours_nodes = [
            node for node in arch.xpath("//field[@name='planned_hours']") if self._view_node(node).tag == "form"
        ]
        self.assertIn("Estimate", [node.get("string") for node in hours_nodes])
        for node in hours_nodes:
            self.assertFalse(node.get("readonly"))
            self.assertNotIn("readonly", node.get("attrs") or "")


@tagged("post_install", "-at_install")
class TestTaskOutsideProject(ProjectAiDrivenCommon):
    """An internal user without Project rights reads the tasks of a project open to employees."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal_user = cls._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        cls.env["project.task.testcase"].create(
            [{"name": "Passed", "task_id": cls.task.id, "state": "passed"}, {"name": "Open", "task_id": cls.task.id}],
        )

    def _kanban_view(self, user):
        kanban_view = self.env.ref("project.view_task_kanban")
        return self.env["project.task"].with_user(user).get_views([(kanban_view.id, "kanban")])

    def _read_every_field(self, user):
        """read() of every task field the user may read that this module and its dependencies
        define: a module installed beside them (hr_timesheet) adds fields of its own rights.
        """
        module = self.env["ir.module.module"].search([("name", "=", MODULE)])
        modules = set((module | module.upstream_dependencies(exclude_states=("uninstallable",))).mapped("name"))
        task = self.task.with_user(user)
        field_names = [
            field_name
            for field_name in task.check_field_access_rights("read", None)
            if set(task._fields[field_name]._modules) <= modules
        ]
        self.env.invalidate_all()
        (task_values,) = task.read(field_names)
        return task_values

    def test_a_reader_outside_project_reads_every_field_but_the_line_counts(self):
        task_values = self._read_every_field(self.internal_user)
        self.assertEqual(task_values["name"], self.task.name)
        self.assertFalse(set(LINE_COUNT_FIELDS) & set(task_values))

    def test_the_task_cards_outside_project_name_no_line_count(self):
        views = self._kanban_view(self.internal_user)
        arch = views["views"]["kanban"]["arch"]
        self.assertFalse(set(LINE_COUNT_FIELDS) & set(etree.fromstring(arch).xpath("//field/@name")))
        self.assertNotRegex(arch, r"\brecord\.(%s)\b" % "|".join(LINE_COUNT_FIELDS))
        self.assertFalse(set(LINE_COUNT_FIELDS) & set(views["models"]["project.task"]))

    def test_a_project_user_reads_and_sees_the_line_counts(self):
        task_values = self._read_every_field(self.user_dev)
        self.assertLessEqual(set(LINE_COUNT_FIELDS), set(task_values))
        self.assertEqual(task_values["testcase_completion"], 50)
        arch = self._kanban_view(self.user_dev)["views"]["kanban"]["arch"]
        self.assertLessEqual(set(LINE_COUNT_FIELDS), set(etree.fromstring(arch).xpath("//field/@name")))
        for raw_field in ("completion_xml", "kanban_checklist", "testcase_completion_xml", "kanban_testcases"):
            self.assertIn("record.%s.raw_value" % raw_field, arch)
