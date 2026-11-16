from lxml import etree

from odoo.tests.common import TransactionCase, new_test_user

from .common import MODULE

VIEW_TAGS = ("form", "list", "kanban", "card", "calendar")
STARS = "//field[@name='priority']"
FORM_STAR = "(//sheet//field[@name='priority'][not(ancestor::field)])[%d]"
# before the title and below the inline lists
PLACES_IN_THE_SHEET = (("//sheet//h1", "before"), ("//sheet/notebook", "after"))
# the form has two by its title (active tasks, archived and template ones) and one in each list
STAR_COUNTS = {
    "project.view_task_form2": 4,
    "project.view_task_card": 1,
    "project.project_task_view_tree_base": 1,
    "project.view_task_tree2": 1,
    "project.view_task_calendar": 1,
}
VIEWS_THE_CLIENT_GETS = {
    "project.view_task_form2": "form",
    "project.view_task_kanban": "kanban",
    "project.view_task_tree2": "list",
    "project.view_task_calendar": "calendar",
}
PROJECT_SHARING_VIEWS = (
    "project.project_sharing_project_task_view_form",
    "project.project_sharing_project_task_view_card",
    "project.project_sharing_project_task_view_tree",
)
# a descendant of the internal list; only the list_view_ref of the read-only depend_on_ids
# of the sharing form names it, and that field shows its own inline list
PROJECT_SHARING_VIEW_HIDING_THE_STAR = "project.open_view_blocked_by_list_view"
HIDDEN_ON_FLAGGED_PROJECTS = "is_ai_driven"


class TestTaskViews(TransactionCase):
    def _combined_arch(self, xmlid):
        return self.env.ref(xmlid)._get_combined_arch()

    def _view_node(self, node):
        """Return the view node belongs to: a list inside a form reads its own fields."""
        return next(node.iterancestors(*VIEW_TAGS))

    def test_star_is_hidden_on_tasks_of_flagged_projects(self):
        for xmlid, star_count in STAR_COUNTS.items():
            stars = self._combined_arch(xmlid).xpath(STARS)
            self.assertGreaterEqual(len(stars), star_count, xmlid)
            for star in stars:
                self.assertEqual(star.get("invisible"), HIDDEN_ON_FLAGGED_PROJECTS, xmlid)

    def test_views_with_a_star_load_the_flag(self):
        """As the web client gets them: the view adds a field its expressions
        name, each list of the form for its own rows.
        """
        # the dependencies group keeps the list of blocking tasks in the form
        user = new_test_user(
            self.env,
            "project_ai_driven_viewer",
            groups="project.group_project_user,project.group_project_task_dependencies",
        )
        for xmlid, view_type in VIEWS_THE_CLIENT_GETS.items():
            view = self.env["project.task"].with_user(user).get_view(self.env.ref(xmlid).id, view_type)
            arch = etree.fromstring(view["arch"])
            stars = arch.xpath(STARS)
            self.assertGreaterEqual(len(stars), STAR_COUNTS.get(xmlid, 1), xmlid)
            flagged_views = {self._view_node(flag) for flag in arch.xpath("//field[@name='is_ai_driven']")}
            for star in stars:
                self.assertIn(self._view_node(star), flagged_views, xmlid)

    def test_project_sharing_views_keep_the_star(self):
        for xmlid in PROJECT_SHARING_VIEWS:
            stars = self._combined_arch(xmlid).xpath(STARS)
            self.assertTrue(stars, xmlid)
            for star in stars:
                self.assertNotEqual(star.get("invisible"), HIDDEN_ON_FLAGGED_PROJECTS, xmlid)

    def test_blocked_by_list_of_project_sharing_hides_the_star(self):
        stars = self._combined_arch(PROJECT_SHARING_VIEW_HIDING_THE_STAR).xpath(STARS)
        self.assertTrue(stars)
        for star in stars:
            self.assertEqual(star.get("invisible"), HIDDEN_ON_FLAGGED_PROJECTS)

    def test_form_star_anchors_take_the_two_stars_of_the_core_form_outside_its_lists(self):
        own_arch = etree.fromstring(self.env.ref("%s.view_task_form2_common" % MODULE).arch)
        anchors = [spec.get("expr") for spec in own_arch.iter("xpath") if "[not(ancestor::field)]" in spec.get("expr")]
        core_arch = etree.fromstring(self.env.ref("project.view_task_form2").arch)
        header_stars = core_arch.xpath("//sheet//field[@name='priority'][@widget='priority_switch']")
        self.assertEqual(len(header_stars), 2)
        self.assertEqual([core_arch.xpath(anchor)[0] for anchor in anchors], header_stars)

    def _move_form_star(self, star_index, place, position):
        """Create the view of another module that moves a star of the form."""
        arch = '<data><xpath expr="%s" position="%s"><xpath expr="%s" position="move"/></xpath></data>' % (
            place,
            position,
            FORM_STAR % star_index,
        )
        return self.env["ir.ui.view"].create(
            {
                "name": "project.task.form.moved.star",
                "model": "project.task",
                "inherit_id": self.env.ref("project.view_task_form2").id,
                "priority": 1,
                "arch": arch,
            },
        )

    def test_form_star_is_hidden_wherever_another_module_puts_it_in_the_sheet(self):
        for star_index in (1, 2):
            for place, position in PLACES_IN_THE_SHEET:
                with self.subTest(star_index=star_index, place=place):
                    moving_view = self._move_form_star(star_index, place, position)
                    arch = self._combined_arch("project.view_task_form2")
                    moving_view.unlink()

                    place_node = arch.xpath(place)[0]
                    moved_star = place_node.getprevious() if position == "before" else place_node.getnext()
                    self.assertEqual(moved_star.get("name"), "priority", place)
                    stars = arch.xpath(STARS)
                    self.assertEqual(len(stars), STAR_COUNTS["project.view_task_form2"], place)
                    for star in stars:
                        self.assertEqual(star.get("invisible"), HIDDEN_ON_FLAGGED_PROJECTS, place)

    def test_form_shows_the_estimate_the_size_and_the_type_together(self):
        arch = self._combined_arch("project.view_task_form2")
        estimate = arch.xpath("//field[@name='priority_level']")[0].getnext()
        size = estimate.getnext()
        self.assertEqual(
            (estimate.get("name"), estimate.get("string"), estimate.get("widget")),
            ("allocated_hours", "Estimate", "float_time"),
        )
        self.assertEqual([size.get("name"), size.getnext().get("name")], ["tshirt_estimate", "task_type"])
        size_field = self.env["project.task"].fields_get(["tshirt_estimate"])["tshirt_estimate"]
        self.assertEqual((size.get("string"), size_field["string"], size_field["readonly"]), (None, "Size", True))

    def test_estimate_is_editable_wherever_the_form_shows_it(self):
        arch = self._combined_arch("project.view_task_form2")
        hours_nodes = [
            node for node in arch.xpath("//field[@name='allocated_hours']") if self._view_node(node).tag == "form"
        ]
        self.assertIn("Estimate", [node.get("string") for node in hours_nodes])
        for node in hours_nodes:
            self.assertFalse(node.get("readonly"))

    def test_test_cases_view_applies_between_the_checklist_and_the_common_view(self):
        """Sibling views apply by priority, then by id: an equal priority leaves
        the order to the ids.
        """
        checklist_view, test_cases_view, common_view = (
            self.env.ref("%s.%s" % (MODULE, name))
            for name in ("view_task_form2", "view_task_form2_testcase", "view_task_form2_common")
        )
        self.assertLess(checklist_view.priority, test_cases_view.priority)
        self.assertLess(test_cases_view.priority, common_view.priority)
