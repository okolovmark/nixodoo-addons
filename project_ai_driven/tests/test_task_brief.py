import sys
from datetime import date, datetime
from unittest.mock import patch

from lxml import etree
from markupsafe import Markup

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

from ..models import html_markdown
from ..models.html_markdown import NOT_MOVED_IMAGE, get_linked_attachment_ids, html_to_markdown
from .common import ProjectAiDrivenCommon
from .test_inline_images import RED, data_uri

EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
DESCRIPTION = (
    "<h1>Goal</h1>"
    '<p>Show the <b>brief</b>, see <a href="https://example.com/spec">the spec</a>.</p>'
    '<p><img src="%s" data-filename="screen.png"> <img src="https://example.com/chart.png" alt="Chart"></p>'
    "<ul><li>First<ul><li>Nested</li></ul></li><li>Second</li></ul>"
    "<table><tr><th>Field</th><th>Value</th></tr><tr><td>Size</td><td>S</td></tr></table>"
)
EXPECTED_DESCRIPTION = """### Goal

Show the **brief**, see [the spec](https://example.com/spec).

![image: screen.png](attachment %d) ![Chart](https://example.com/chart.png)

- First
  - Nested
- Second

| Field | Value |
| --- | --- |
| Size | S |"""


def get_sections(brief):
    """{title: body} of the sections of a brief, the header under ""."""
    header, *chunks = brief.rstrip("\n").split("\n\n## ")
    sections = {"": header}
    for chunk in chunks:
        title, _separator, body = chunk.partition("\n\n")
        sections[title] = body
    return sections


@tagged("post_install", "-at_install")
class TestHtmlToMarkdown(TransactionCase):
    def assertMarkdown(self, html_content, markdown, **kwargs):
        self.assertEqual(html_to_markdown(html_content, **kwargs), markdown)

    def test_empty_texts_give_nothing(self):
        for html_content in (False, None, "", "  ", Markup("<p><br></p>"), "<html><head></head></html>"):
            self.assertMarkdown(html_content, "")

    def test_headings_take_the_level_given_up_to_six(self):
        self.assertMarkdown("<h1>One</h1><h2>Two</h2>", "# One\n\n## Two")
        self.assertMarkdown("<h1>One</h1><h4>Four</h4>", "### One\n\n###### Four", heading_level=3)

    def test_paragraphs_emphasis_links_and_code(self):
        self.assertMarkdown(
            "<p>A <b>bold </b>and <em>light</em> <s>old</s> word<br>next</p>"
            '<p><a href="https://example.com/a b">the spec</a> <a href="https://example.com">https://example.com</a>'
            " <code>a  `b`</code></p>",
            "A **bold** and *light* ~~old~~ word\nnext\n\n"
            "[the spec](https://example.com/a%20b) https://example.com `` a `b` ``",
        )

    def test_whitespace_is_normalised_and_no_markup_is_left(self):
        self.assertMarkdown(
            "<div>&nbsp; spaced&nbsp;&nbsp;out \n words </div><p>a<br><br><br><br>b</p>"
            "<!-- note --><script>alert(1)</script><style>p {}</style><p><span>in</span><font>line</font></p>",
            "spaced out words\n\na\n\nb\n\ninline",
        )

    def test_nested_lists_as_the_editor_nests_them(self):
        self.assertMarkdown(
            "<ul><li>a<ul><li>a1</li><li>a2<ol><li>x</li><li>y</li></ol></li></ul></li><li>b</li></ul>",
            "- a\n  - a1\n  - a2\n    1. x\n    2. y\n- b",
        )
        self.assertMarkdown(
            '<ol start="3"><li>three</li><li class="oe-nested"><ol><li>nested</li></ol></li><li>four</li></ol>',
            "3. three\n   1. nested\n4. four",
        )

    def test_checklists_keep_their_ticks(self):
        self.assertMarkdown(
            '<ul class="o_checklist"><li class="o_checked">Done</li><li>To do</li>'
            '<li class="oe-nested"><ul class="o_checklist">'
            '<li class="o_checked">Nested done</li><li class="oe-nested"><ul class="o_checklist"><li>Deeper</li></ul></li>'
            "</ul></li>"
            '<li class="o_checked">Done <b>too</b><ul class="o_checklist"><li>Inside</li></ul></li></ul>',
            "- [x] Done\n- [ ] To do\n  - [x] Nested done\n    - [ ] Deeper\n- [x] Done **too**\n  - [ ] Inside",
        )

    def test_checklists_and_plain_lists_nest_in_each_other(self):
        self.assertMarkdown(
            '<ul><li class="o_checked">Plain<ul class="list-unstyled o_checklist"><li class="o_checked">Ticked</li>'
            "</ul></li></ul>"
            '<ul class="o_checklist"><li>Box<ol><li>one</li></ol></li></ul>',
            "- Plain\n  - [x] Ticked\n\n+ [ ] Box\n  1. one",
        )

    def test_an_item_in_a_block_in_a_checklist_has_no_box(self):
        """The editor draws the box of an item right in the checklist only."""
        self.assertMarkdown('<ul class="o_checklist"><li>a</li><div><li>b</li></div></ul>', "- [ ] a\n- b")
        self.assertMarkdown(
            '<ul class="o_checklist"><li>a</li><div>t<li class="o_checked">b</li></div></ul>', "- [ ] a\n\nt\n\n- b"
        )
        self.assertMarkdown('<ul class="o_checklist"><div><li>[ ] b</li></div></ul>', "- \\[ ] b")

    def test_an_ordered_list_of_the_checklist_class_keeps_its_numbers_without_a_box(self):
        """The editor draws the box in a bullet list only."""
        self.assertMarkdown(
            '<ol class="o_checklist"><li>a</li><li class="o_checked">b</li><li>[ ] c</li></ol>',
            "1. a\n2. b\n3. \\[ ] c",
        )
        self.assertMarkdown(
            '<ul class="o_checklist"><li>a<ol class="o_checklist"><li class="o_checked">x</li></ol></li></ul>',
            "- [ ] a\n  1. x",
        )

    def test_an_oe_nested_item_holding_text_goes_on_under_the_item_before_it(self):
        """The editor shows it as a block, with no marker, and a browser gives it no number."""
        self.assertMarkdown(
            '<ol><li>alpha</li><li class="oe-nested">bravo</li><li>charlie</li></ol>', "1. alpha\n   bravo\n2. charlie"
        )
        self.assertMarkdown(
            '<ol reversed><li>a</li><li class="oe-nested" value="7">b</li><li>c</li><li class="oe-nested"></li></ol>',
            "2. a\n   b\n1) c",
        )
        self.assertMarkdown(
            '<ol><li>a<ul><li>x</li></ul></li><li class="oe-nested">b</li><li class="oe-nested"><h3>h</h3></li>'
            "<li>c</li></ol>",
            "1. a\n   - x\n\n   b\n\n   ### h\n2. c",
        )
        self.assertMarkdown('<ol><li><br></li><li class="oe-nested">[x] t</li><li>c</li></ol>', "1.\n   \\[x] t\n2. c")

    def test_an_oe_nested_item_holding_text_in_a_checklist_goes_on_without_a_box(self):
        self.assertMarkdown(
            '<ul class="o_checklist"><li>a</li><li class="oe-nested o_checked">t</li><li class="oe-nested">[x] u</li>'
            '<li class="oe-nested">v<ul class="o_checklist"><li class="o_checked">w</li></ul></li><li>b</li></ul>',
            "- [ ] a\n  t\n  [x] u\n  v\n  - [x] w\n- [ ] b",
        )

    def test_an_oe_nested_item_holding_text_with_no_item_before_it_stands_between_the_parts_of_its_list(self):
        self.assertMarkdown('<ol><li class="oe-nested">bravo</li><li>charlie</li></ol>', "bravo\n\n1. charlie")
        self.assertMarkdown(
            '<ol><li>a</li><pre>c</pre><li class="oe-nested">b</li><li>d</li></ol>', "1. a\n\n```\nc\n```\n\nb\n\n2. d"
        )
        self.assertMarkdown('<ul class="o_checklist"><li class="oe-nested">t</li><li>b</li></ul>', "t\n\n- [ ] b")
        self.assertMarkdown('<ol><blockquote><li class="oe-nested">t</li></blockquote><li>b</li></ol>', "> t\n\n1. b")
        self.assertMarkdown('<ol><li>a</li>t<li class="oe-nested">b</li><li>c</li></ol>', "1. a\n\nt\n\nb\n\n2. c")

    def test_an_empty_bullet_item_keeps_its_marker_for_the_oe_nested_item_holding_text_right_after_it(self):
        """The browser shows the text under the item's bullet, and under its box in a checklist: the text goes on
        the box's line, as GFM draws a box only before text on its line, a heading under it, the box then a task in
        the raw text only; another empty bullet item is left out.
        """
        for html_content, markdown in (
            ('<ul><li>a</li><li></li><li class="oe-nested">t</li></ul>', "- a\n-\n  t"),
            ('<ul><li><br></li><li><br></li><li class="oe-nested">[x] t</li><li>b</li></ul>', "-\n  \\[x] t\n- b"),
            ('<p>p</p><ul><li></li><li class="oe-nested">t</li></ul>', "p\n\n-\n  t"),
            (
                '<ul class="o_checklist"><li></li><li class="oe-nested">[x] t</li><li>b</li></ul>',
                "- [ ] [x] t\n- [ ] b",
            ),
            (
                '<ul class="o_checklist"><li class="o_checked"></li><li class="oe-nested"><h3>h</h3></li></ul>',
                "- [x]\n\n  ### h",
            ),
            (
                '<ul><li>a</li><li></li><li class="oe-nested"><ul><li>x</li></ul></li><li></li><li>b</li></ul>',
                "- a\n  - x\n- b",
            ),
        ):
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_lists_of_a_kind_next_to_each_other_stay_apart(self):
        self.assertMarkdown(
            "<ul><li>a</li></ul><ul><li>b</li></ul><div><ul><li>c</li></ul></div>"
            '<ol><li>one</li></ol><ol start="5"><li>five</li><li>six<ol><li>x</li></ol></li></ol>'
            "<ul><li>d</li></ul><p>then</p><ul><li>e</li></ul>",
            "- a\n\n+ b\n\n- c\n\n1. one\n\n5) five\n6) six\n   1. x\n\n- d\n\nthen\n\n- e",
        )
        self.assertMarkdown(
            "<blockquote><ul><li>a</li></ul><ul><li>b</li></ul></blockquote>"
            "<ul><li>item<ul><li>a</li></ul><ul><li>b</li></ul></li></ul>",
            "> - a\n>\n> + b\n\n- item\n  - a\n  + b",
        )

    def test_the_other_bullet_of_a_list_makes_no_rule(self):
        self.assertMarkdown("<ul><li>a</li></ul><ul><li>**</li></ul>", "- a\n\n+ **")

    def test_nested_lists_next_to_each_other_stay_apart(self):
        self.assertMarkdown(
            '<ol><li>a</li><ol><li>x</li></ol><ol start="5"><li>y</li></ol></ol>', "1. a\n   1. x\n   5) y"
        )
        self.assertMarkdown(
            '<ul><li>a</li><li class="oe-nested"><ul><li>x</li></ul></li><li class="oe-nested"><ul><li>y</li></ul></li>'
            '<li>b<ul><li>z</li></ul></li><ul><li>w</li></ul><li>c</li><li class="oe-nested"><ul><li>v</li></ul></li></ul>',
            "- a\n  - x\n  + y\n- b\n  - z\n  + w\n- c\n  - v",
        )
        self.assertMarkdown(
            "<ul><li>a</li><ul><li>x</li></ul>Between<ul><li>y</li></ul></ul>", "- a\n  - x\n\nBetween\n\n  - y"
        )
        self.assertMarkdown(
            '<ul><li>a</li><li class="oe-nested"><ul><li>x</li></ul><ul><li>y</li></ul></li></ul>', "- a\n  - x\n  + y"
        )

    def test_a_list_after_one_opening_with_a_nested_list_stays_apart(self):
        self.assertMarkdown(
            '<ul><li class="oe-nested"><ol><li>x</li></ol></li><li>y</li></ul><ul><li>z</li></ul>',
            "  1. x\n- y\n\n+ z",
        )
        self.assertMarkdown(
            '<ul><li class="oe-nested"><ul><li>x</li></ul></li></ul><ul><li>z</li></ul>', "  - x\n\n+ z"
        )

    def test_a_list_opening_with_a_nested_list_right_after_a_list_stays_apart(self):
        """Its nested lines open at its own level, which CommonMark reads as at the start of a text."""
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul><li class="oe-nested"><ul><li>b</li></ul></li><li>c</li></ul>', "- a\n\n+ b\n+ c"
        )
        self.assertMarkdown(
            '<ol><li>a</li></ol><ol><li class="oe-nested"><ol><li>b</li></ol></li><li>c</li></ol>',
            "1. a\n\n1) b\n1. c",
        )
        self.assertMarkdown('<ul><li>a</li></ul><ul><li class="oe-nested"><ul><li>b</li></ul></li></ul>', "- a\n\n+ b")
        self.assertMarkdown(
            '<ul><li>a</li><li class="oe-nested"><ul><li>x</li></ul></li>'
            '<li class="oe-nested"><ul><li class="oe-nested"><ul><li>y</li></ul></li><li>z</li></ul></li></ul>',
            "- a\n  - x\n  + y\n  + z",
        )
        self.assertMarkdown(
            '<ul><li>a<ul><li>x</li></ul><ul><li class="oe-nested"><ul><li>y</li></ul></li><li>z</li></ul></li></ul>',
            "- a\n  - x\n  + y\n  + z",
        )
        self.assertMarkdown(
            '<p>p</p><ul><li class="oe-nested"><ul><li>b</li></ul></li><li>c</li></ul>', "p\n\n  - b\n- c"
        )

    def test_a_rule_opening_a_bullet_item_stays_in_it(self):
        self.assertMarkdown("<ul><li>a</li><li><hr></li></ul>", "- a\n- * * *")
        self.assertMarkdown("<ul><li>a<ul><li><hr></li></ul></li></ul>", "- a\n  - * * *")
        self.assertMarkdown("<ul><li>a</li></ul><ul><li><hr></li></ul>", "- a\n\n+ * * *")
        self.assertMarkdown(
            "<ol><li><hr></li></ol><ul><li>a<hr>b</li></ul><hr>", "1. ---\n\n- a\n\n  ---\n\n  b\n\n---"
        )

    def test_a_nested_list_not_numbered_from_one_is_a_paragraph_apart_from_the_item_text(self):
        """CommonMark lets only an ordered list from 1 start right under a paragraph: another is its text."""
        self.assertMarkdown(
            '<ul><li>step<ol start="3"><li>x</li></ol></li><li>step</li>'
            '<li class="oe-nested"><ol start="4"><li>y</li></ol></li><li>last</li></ul>',
            "- step\n\n  3. x\n- step\n\n  4. y\n- last",
        )
        self.assertMarkdown(
            '<ol><li>step</li><li class="oe-nested"><ol start="0"><li>x</li></ol></li></ol>', "1. step\n\n   0. x"
        )
        self.assertMarkdown(
            "<ul><li>step<ol><li>x</li></ol></li><li>step<ul><li>y</li></ul></li>"
            '<li><h3>Step</h3><ol start="3"><li>z</li></ol></li>'
            '<li>a<ul><li>w</li></ul></li><li class="oe-nested"><ol start="5"><li>v</li></ol></li></ul>',
            "- step\n  1. x\n- step\n  - y\n- ### Step\n  3. z\n- a\n  - w\n  5. v",
        )

    def test_a_nested_list_not_numbered_from_one_right_under_another_block_than_a_paragraph_is_not_apart(self):
        for block_html, block_markdown in (
            ("<hr>", "---"),
            ("<blockquote>q</blockquote>", "> q"),
            ("<table><tr><td>t</td></tr></table>", "| t |\n   | --- |"),
            ("<pre>c</pre>", "```\n   c\n   ```"),
        ):
            with self.subTest(block=block_markdown):
                self.assertMarkdown(
                    '<ol><li><p>a</p>%s<ol start="3"><li>b</li></ol></li></ol>' % block_html,
                    "1. a\n\n   %s\n   3. b" % block_markdown,
                )

    def test_a_list_opening_with_nested_lists_two_levels_deep_right_after_a_list_keeps_its_items(self):
        """Every opening level opens at the level of the list: no line loses its marker or joins the item above."""
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul><li class="oe-nested"><ul><li class="oe-nested"><ul><li>b</li></ul></li>'
            "<li>x</li></ul></li><li>c</li></ul>",
            "- a\n\n+ b\n+ x\n+ c",
        )
        self.assertMarkdown(
            '<ol><li>a</li></ol><ol><li class="oe-nested"><ol><li class="oe-nested"><ol><li>b</li></ol></li>'
            "<li>x</li></ol></li><li>c</li></ol>",
            "1. a\n\n1) b\n1. x\n1) c",
        )
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul><li class="oe-nested"><ul><li class="oe-nested"><ul><li>b</li></ul></li><li>x</li>'
            '<li class="oe-nested"><ul><li>y</li></ul></li></ul></li><li>c</li></ul>',
            "- a\n\n+ b\n+ x\n  - y\n+ c",
        )
        self.assertMarkdown(
            '<ol><li>a</li></ol><ol><li class="oe-nested"><ol><li class="oe-nested"><ol><li>b</li></ol></li><li>x</li>'
            '<li class="oe-nested"><ol><li>y</li></ol></li></ol></li><li>c</li></ol>',
            "1. a\n\n1) b\n1. x\n   1. y\n1) c",
        )

    def test_the_items_of_a_list_opening_with_nested_lists_take_their_marker_from_the_list_before_it(self):
        """Not from the nested lists it opens with, so that its first item stays apart from their last."""
        self.assertMarkdown(
            '<ol><li>a</li></ol><ol><li class="oe-nested"><ul><li>b</li></ul></li>'
            '<li class="oe-nested"><ol><li>x</li></ol></li><li>c</li></ol>',
            "1. a\n\n- b\n1. x\n1) c",
        )
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul><li class="oe-nested"><ol><li>b</li></ol></li>'
            '<li class="oe-nested"><ul><li>x</li></ul></li><li>c</li></ul>',
            "- a\n\n1. b\n- x\n+ c",
        )

    def test_a_nested_list_after_the_first_item_of_a_list_right_after_a_list_stays_under_that_item(self):
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul><li class="oe-nested"><ul><li>b</li></ul></li><li>c</li>'
            '<li class="oe-nested"><ul><li>d</li></ul></li></ul>',
            "- a\n\n+ b\n+ c\n  - d",
        )

    def test_a_rule_right_in_a_bullet_list_is_a_rule_between_its_items(self):
        self.assertMarkdown("<ul><li>a</li><hr><li>b</li></ul>", "- a\n\n---\n\n- b")
        self.assertMarkdown("<ul><li>x</li></ul><ul><li>a</li><hr><li>b</li></ul>", "- x\n\n+ a\n\n---\n\n- b")
        self.assertMarkdown("<ul><li>a</li><div><hr></div><li>b</li></ul>", "- a\n\n---\n\n- b")

    def test_a_block_right_in_a_bullet_list_opening_with_a_rule_keeps_it_a_rule(self):
        """Not an item holding a rule: a browser shows no item there."""
        self.assertMarkdown("<ul><li>a</li><div><hr>Note</div><li>b</li></ul>", "- a\n\n---\n\nNote\n\n- b")

    def test_an_ordered_list_keeps_its_numbers_across_a_rule_right_in_it(self):
        self.assertMarkdown("<ol><li>a</li><hr><li>b</li></ol>", "1. a\n\n---\n\n2. b")
        self.assertMarkdown("<ol><li>a</li><div><hr></div><li>b</li></ol>", "1. a\n\n---\n\n2. b")

    def test_a_block_holding_text_right_in_a_list_stands_between_its_items_unnumbered(self):
        """As loose text does: the list goes on after it as at the start of a text, or after a list where the
        block ends in one, with the numbers a browser gives its items.
        """
        self.assertMarkdown("<ol><li>a</li><div>x</div><li>b</li></ol>", "1. a\n\nx\n\n2. b")
        self.assertMarkdown("<ol><li>a</li><p>x</p><li>b</li><li>c</li></ol>", "1. a\n\nx\n\n2. b\n3. c")
        self.assertMarkdown("<ol><li>a</li><div><hr>Note</div><li>b</li></ol>", "1. a\n\n---\n\nNote\n\n2. b")
        self.assertMarkdown("<ul><li>a</li><h3>Step</h3><li>b</li></ul>", "- a\n\n### Step\n\n- b")
        self.assertMarkdown("<ol><li>a</li><div>x<ol><li>y</li></ol></div><li>b</li></ol>", "1. a\n\nx\n\n1. y\n\n2) b")
        self.assertMarkdown("<ol><li>a</li><div><ol><li>y</li></ol>x</div><li>b</li></ol>", "1. a\n\n1) y\n\nx\n\n2. b")
        self.assertMarkdown(
            '<ol><li>a</li><div>x<ul><li>y</li></ul></div><li class="oe-nested"><ul><li>z</li></ul></li><li>b</li></ol>',
            "1. a\n\nx\n\n- y\n\n+ z\n2. b",
        )
        self.assertMarkdown(
            "<ul><li>a<ol><li>x</li><p>note</p><li>y</li></ol></li></ul>", "- a\n  1. x\n\n  note\n\n  2. y"
        )

    def test_a_block_holding_an_item_of_the_list_keeps_it_numbered(self):
        self.assertMarkdown("<ol><li>a</li><div><li>b</li></div><li>c</li></ol>", "1. a\n2. b\n3. c")

    def test_what_a_block_holds_beside_an_item_of_the_list_stands_between_its_parts(self):
        """A browser numbers the item in the list and shows the rest as it shows it right in the list."""
        self.assertMarkdown("<ol><li>a</li><div>x<li>b</li></div><li>c</li></ol>", "1. a\n\nx\n\n2. b\n3. c")
        self.assertMarkdown("<ol><li>a</li><div><li>b</li>y</div><li>c</li></ol>", "1. a\n2. b\n\ny\n\n3. c")
        self.assertMarkdown("<ol><li>a</li><div><li>b</li></div>y<li>c</li></ol>", "1. a\n2. b\n\ny\n\n3. c")
        self.assertMarkdown("<ol><li>a</li><div><div>x</div><li>b</li></div><li>c</li></ol>", "1. a\n\nx\n\n2. b\n3. c")
        self.assertMarkdown(
            "<ol><li>a</li>Loose<div>x<li>b</li></div><li>c</li></ol>", "1. a\n\nLoose\n\nx\n\n2. b\n3. c"
        )
        self.assertMarkdown("<ul><li>a</li><div><hr><li>b</li></div><li>c</li></ul>", "- a\n\n---\n\n- b\n- c")

    def test_a_reversed_list_counts_the_items_its_blocks_hold(self):
        self.assertMarkdown("<ol reversed><li>a</li><div>x<li>b</li></div><li>c</li></ol>", "3. a\n\nx\n\n2. b\n1) c")
        self.assertMarkdown(
            "<ol reversed><li>a</li><blockquote><li>b</li></blockquote><li>c</li></ol>", "3. a\n2) > b\n1. c"
        )

    def test_a_quote_holding_only_lists_right_in_a_list_stands_between_its_parts(self):
        self.assertMarkdown(
            "<ul><li>a</li><blockquote><ul><li>q</li></ul></blockquote><li>b</li></ul>", "- a\n\n> - q\n\n- b"
        )
        self.assertMarkdown(
            "<ol><li>a</li><blockquote><ol><li>q</li></ol></blockquote><li>b</li></ol>", "1. a\n\n> 1. q\n\n2. b"
        )

    def test_items_a_code_block_cut_off_their_list_show_as_a_bullet_list(self):
        """The HTML parser closes a bullet list at a code block right in it: the items after it stand in no list,
        where a browser shows them as bullet items, whatever the list was.
        """
        self.assertMarkdown("<ul><li>a</li><pre>x</pre><li>b</li><li>c</li></ul>", "- a\n\n```\nx\n```\n\n- b\n- c")
        self.assertMarkdown("<ol><li>a</li></ol><pre>x</pre><li>b</li>", "1. a\n\n```\nx\n```\n\n- b")

    def test_stray_items_show_as_a_bullet_list_wherever_they_stand(self):
        self.assertMarkdown("<p>p</p><li>a</li><li>b</li>", "p\n\n- a\n- b")
        self.assertMarkdown("<li>a</li>t<li>b</li> <li>c</li>", "- a\n\nt\n\n- b\n- c")
        self.assertMarkdown("<div><li>a</li></div><blockquote><li>b</li><li>c</li></blockquote>", "- a\n\n> - b\n> - c")
        self.assertMarkdown("<table><tr><td><li>a</li><li>b</li></td></tr></table>", "| - a - b |\n| --- |")

    def test_stray_items_right_after_a_list_of_their_marker_take_the_other_one(self):
        """A browser shows them apart from the list, whose items stand further in."""
        self.assertMarkdown("<ul><li>a</li></ul><li>b</li>", "- a\n\n+ b")
        self.assertMarkdown("<ul><li>a</li></ul><li>b</li><p>t</p>", "- a\n\n+ b\n\nt")
        self.assertMarkdown("<ul><li>a</li></ul><div><li>b</li></div>", "- a\n\n+ b")
        self.assertMarkdown("<li>a</li><ul><li>b</li></ul>", "- a\n\n+ b")

    def test_a_stray_item_holds_what_an_item_of_a_list_holds(self):
        self.assertMarkdown("<li>a<ul><li>b</li></ul></li><li>c</li>", "- a\n  - b\n- c")
        self.assertMarkdown('<li>a</li><li class="oe-nested"><ul><li>n</li></ul></li><li>b</li>', "- a\n  - n\n- b")
        self.assertMarkdown("<li>a<div><li>b</li></div></li>", "- a\n  - b")

    def test_an_item_in_a_block_in_a_list_is_no_stray_item(self):
        """The list around it writes it, as an item of its own or in the item the block makes."""
        self.assertMarkdown("<ul><li>a</li><div>t<li>b</li></div></ul>", "- a\n\nt\n\n- b")
        self.assertMarkdown("<ul><li>a</li><blockquote><li>b</li></blockquote></ul>", "- a\n- > b")

    def test_a_block_right_in_a_list_is_searched_for_its_items_outside_its_nested_lists_only(self):
        """The items of a nested list are its own: a chain of blocks each holding a list costs its length only."""
        created_elements = []

        class CountedElement(etree.ElementBase):
            def _init(self):
                created_elements.append(self.tag)

        parser = etree.HTMLParser()
        parser.set_element_class_lookup(etree.ElementDefaultClassLookup(element=CountedElement))
        nested_items = "<li>x</li>" * 100
        for list_html, holds_item in (
            ("<ul><div>t<ol>%s</ol><div><li>a</li></div></div></ul>" % nested_items, True),
            ("<ul><div>t<ol>%s</ol></div></ul>" % nested_items, False),
        ):
            block = etree.fromstring(list_html, parser).find("body/ul/div")
            created_elements.clear()
            self.assertEqual(html_markdown._holds_list_item(block, ()), holds_item)
            self.assertLess(len(created_elements), 10, "the items of the nested list are not searched")

    def test_blocks_right_in_a_list_read_the_last_line_of_the_lines_before_them(self):
        """What a block right in a list reads of the lines before it is carried on from block to block, and
        starts again where the list's lines do.
        """
        self.assertMarkdown("<ul><li>a</li><p></p><div><ul><li>b</li></ul><br></div></ul>", "- a\n\n+ b")
        self.assertMarkdown(
            "<ul><li>a</li><div>t</div><div><ol><li>b</li></ol></div><div><ol><li>c</li></ol><br></div></ul>",
            "- a\n\nt\n\n  1. b\n\n1) c",
        )
        self.assertMarkdown(
            "<ol reversed><li>v</li><p></p><li>x</li><div><ol><li>y</li></ol><br></div></ol>",
            "2. v\n1) x\n\n1. y",
        )

    def _count_lines_read(self, html_content):
        """How many lines html_to_markdown reads for the last line of a block, as _get_last_line does."""
        get_last_line = html_markdown._get_last_line
        read_line_counts = []

        def read_last_line(lines, *args):
            read_line_counts.append(len(lines))
            return get_last_line(lines, *args)

        with patch.object(html_markdown, "_get_last_line", read_last_line):
            html_to_markdown(html_content)
        return sum(read_line_counts)

    def _count_lines_run(self, html_content):
        """How many lines of the converter html_to_markdown runs: its work, whatever code does it."""
        converter_globals = vars(html_markdown)
        line_count = 0

        def count_line(_frame, event, _arg):
            nonlocal line_count
            if event == "line":
                line_count += 1
            return count_line

        def trace_converter(frame, _event, _arg):
            return count_line if frame.f_globals is converter_globals else None

        previous_trace = sys.gettrace()
        sys.settrace(trace_converter)
        try:
            html_to_markdown(html_content)
        finally:
            sys.settrace(previous_trace)
        return line_count

    def test_a_run_of_empty_ordered_items_costs_its_length_only(self):
        """The empty items waiting are made items once a line follows them, not at each block after them, or a
        run of them takes quadratic time.
        """
        for empty_item in ("<li><br></li>", "<li></li>", "<li><p><br></p></li>", "<li><br></li><div></div>"):
            for list_end in ("", "<li>z</li>"):
                with self.subTest(empty_item=empty_item, list_end=list_end):
                    short_run, long_run = (
                        self._count_lines_run("<ol>%s%s</ol>" % (empty_item * run_length, list_end))
                        for run_length in (300, 600)
                    )
                    self.assertLess(long_run, 2.5 * short_run)

    def test_a_chain_of_blocks_holding_an_item_right_in_a_list_costs_its_length_only(self):
        """The chain is searched for items once, and what it holds goes to the list past no block of it, or a
        long chain takes its length times what it holds.
        """
        for block in ("<p>y</p>", "<li>y</li>"):
            with self.subTest(block=block):
                short_chain, long_chain = (
                    "<ol>%s<li>x</li>%s%s</ol>" % ("<div>" * depth, block * 100, "</div>" * depth) for depth in (10, 40)
                )
                self.assertLess(self._count_lines_run(long_chain), 1.5 * self._count_lines_run(short_chain))

    def test_backslashes_before_a_bang_deep_in_elements_are_walked_once(self):
        """The "!" waits for the backslashes before it up to its paragraph, each element walking back only those
        before its own, or a long run deep in nested elements takes its length times their depth.
        """
        short_nesting, long_nesting = (
            '<p>%s%s!<a href="https://e.com/">u</a>%s</p>' % ("<span>" * depth, "\\" * 5000, "</span>" * depth)
            for depth in (10, 40)
        )
        self.assertLess(self._count_lines_run(long_nesting), 1.5 * self._count_lines_run(short_nesting))

    def test_empty_blocks_right_in_a_list_read_its_nested_lines_once(self):
        """Each block between the parts of a list reads the last line of the list at its level: on from the lines
        read for the block before, or a run of empty blocks after many nested lines takes quadratic time.
        """
        count = 300
        for html_content in (
            "<ul><li>a</li><ul>%s</ul>%s</ul>" % ("<li>x</li>" * count, "<p><br></p>" * count),
            "<ul><li>a</li>%s</ul>" % ("<ul><li>x</li></ul><div></div>" * count),
            '<ol><li>a</li><li class="oe-nested"><ol>%s</ol></li>%s</ol>'
            % ("<li>x</li>" * count, "<div></div>" * count),
        ):
            self.assertLess(self._count_lines_read(html_content), 5 * count)

    def test_lists_writing_nothing_read_nothing_of_the_block_before_them(self):
        """A list reads the block before it for its marker only as it writes a line, or a run of empty lists after
        a long block takes quadratic time.
        """
        count = 300
        long_list = "<ul>%s</ul>" % ("<li>x</li>" * count)
        for html_content in (
            long_list + "<ul></ul>" * count,
            "<ul><li>a<ul>%s</ul></li>%s</ul>" % ("<li>x</li>" * count, "<ul></ul>" * count),
            long_list + "<div><ul><ul></ul></ul></div>" * count,
            long_list + "<ol><li><br></li></ol>" * count,
        ):
            self.assertLess(self._count_lines_read(html_content), count)

    def test_a_list_in_a_block_right_in_a_list_opening_a_list_takes_no_indent_of_its_own(self):
        """The list the block is in indents it already: four spaces would make its lines text of the block."""
        self.assertMarkdown(
            '<ul><li class="oe-nested"><ol><div>t<ul><ul><li>x</li></ul></ul></div></ol></li></ul>', "  t\n  - x"
        )

    def test_a_plain_item_holding_only_lists_shows_its_marker(self):
        """Only the editor's oe-nested item is a container a browser shows no marker for."""
        self.assertMarkdown("<ol><li>a</li><li><ol><li>x</li></ol></li><li>b</li></ol>", "1. a\n2. 1. x\n3. b")
        self.assertMarkdown("<ol><li><ul><li>x</li></ul></li><li>b</li></ol>", "1. - x\n2. b")
        self.assertMarkdown(
            '<ul><li>a</li><li><ol><li>x</li></ol><ol start="3"><li>y</li></ol></li></ul>', "- a\n- 1. x\n  3) y"
        )

    def test_lines_nested_after_a_rule_right_in_a_list_open_as_at_the_start_of_a_text(self):
        self.assertMarkdown(
            '<ul><li>a</li><hr><li class="oe-nested"><ul><li class="oe-nested"><ul><li>b</li></ul></li></ul></li>'
            "<li>c</li></ul>",
            "- a\n\n---\n\n  - b\n- c",
        )
        self.assertMarkdown(
            '<ol><li class="oe-nested"><ol><hr><li class="oe-nested"><ol start="3"><li>b</li><li>c</li></ol></li>'
            "</ol></li></ol>",
            "  ---\n  3. b\n  4. c",
        )

    def test_a_rule_opening_a_nested_list_is_no_underline_of_the_item_text(self):
        self.assertMarkdown(
            '<ul><li>a</li><li class="oe-nested"><ul><hr><li>b</li></ul></li></ul>', "- a\n\n  ---\n  - b"
        )

    def test_a_list_in_a_div_right_after_a_list_of_its_marker_takes_the_other_one(self):
        self.assertMarkdown("<ul><li>a</li></ul><div><ul><li>b</li></ul></div>", "- a\n\n+ b")

    def test_a_list_loose_in_a_table_right_after_a_list_of_its_marker_takes_the_other_one(self):
        self.assertMarkdown(
            "<ol><li>a</li></ol><table><ol><li>b</li></ol><tr><td>c</td></tr></table>"
            "<ul><li>d</li></ul><table><tbody><ul><li>e</li></ul><tr><td>f</td></tr></tbody></table>",
            "1. a\n\n1) b\n\n| c |\n| --- |\n\n- d\n\n+ e\n\n| f |\n| --- |",
        )

    def test_a_nested_list_opening_with_a_nested_list_stays_right_under_the_item_text(self):
        self.assertMarkdown(
            '<ul><li>a<ul><li class="oe-nested"><ul><li>b</li></ul></li></ul></li></ul>', "- a\n    - b"
        )

    def test_lines_nested_after_loose_text_opening_a_list_right_after_a_list_open_under_it(self):
        """The loose text parts the list from the one before it: its nested lines and its items go on as at the
        start of a text, in one list.
        """
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul>Loose<li class="oe-nested"><ul><li>b</li></ul></li></ul>', "- a\n\nLoose\n\n  - b"
        )
        self.assertMarkdown(
            '<ul><li>a</li></ul><ul>Loose<li class="oe-nested"><ul><li>b</li></ul></li><li>c</li></ul>',
            "- a\n\nLoose\n\n  - b\n- c",
        )

    def test_lines_nested_after_loose_text_in_a_list_stand_two_spaces_in_whatever_its_marker(self):
        self.assertMarkdown(
            '<ol start="10"><li>x</li>Loose<li class="oe-nested"><ol><li>b</li></ol></li><li>c</li></ol>',
            "10. x\n\nLoose\n\n  1. b\n11) c",
        )

    def test_loose_text_of_a_nested_list_is_no_text_of_the_item_above_or_below_it(self):
        self.assertMarkdown(
            '<ul><li>a</li><li class="oe-nested"><ul>Loose<li>b</li></ul></li></ul>', "- a\n\n  Loose\n  - b"
        )
        self.assertMarkdown('<ol start="3"><li class="oe-nested"><ol>Loose</ol></li><li>c</li></ol>', "  Loose\n\n3. c")

    def test_a_list_opening_with_nested_lists_two_levels_deep_stays_a_list(self):
        """Its nested lines stand two spaces in at most, where CommonMark still reads a list: four make code."""
        opening = (
            '<ul><li class="oe-nested"><ul><li class="oe-nested"><ul><li>b</li></ul></li></ul></li><li>c</li></ul>'
        )
        self.assertMarkdown(opening, "  - b\n- c")
        self.assertMarkdown("<p>p</p>" + opening, "p\n\n  - b\n- c")
        self.assertMarkdown("<ul><li>a%s</li></ul>" % opening, "- a\n    - b\n  - c")

    def test_the_items_of_an_ordered_list_opening_with_one_of_their_delimiter_keep_their_numbers(self):
        """A browser numbers each list from its own start: with the delimiter of the list it opens with, an item
        would go on with its numbers.
        """
        self.assertMarkdown('<ol><li class="oe-nested"><ol><li>b</li></ol></li><li>c</li></ol>', "  1. b\n1) c")
        self.assertMarkdown(
            '<ul><li>a</li></ul><ol><li class="oe-nested"><ol start="3"><li>b</li></ol></li><li>c</li></ol>',
            "- a\n\n3. b\n1) c",
        )
        self.assertMarkdown(
            '<ol><li class="oe-nested"><ol><li class="oe-nested"><ol><li>b</li></ol></li><li>x</li></ol></li>'
            "<li>c</li></ol>",
            "  1. b\n  1) x\n1. c",
        )
        self.assertMarkdown(
            '<ol><li class="oe-nested"><ol><li>t1<ul><li>t2</li></ul></li></ol></li><li>t3</li></ol>',
            "  1. t1\n     - t2\n1) t3",
        )

    def test_a_checklist_item_opening_with_a_list_opening_with_nested_lines_has_its_box_alone(self):
        self.assertMarkdown(
            '<ul class="o_checklist"><li><ol><li class="oe-nested"><ul><li>x</li></ul></li><li>y</li></ol>'
            "Then</li></ul>",
            "- [ ]\n\n  - x\n  1. y\n\n  Then",
        )
        self.assertMarkdown(
            '<ul class="o_checklist"><li><ul><li class="oe-nested"><ul><hr><li>b</li></ul></li><li>x</li></ul>'
            "Then</li></ul>",
            "- [ ]\n\n  ---\n  - b\n  - x\n\n  Then",
        )

    def test_an_item_opening_with_a_list_opening_with_nested_lines_keeps_its_lines(self):
        """The marker of the item indents the nested lines: two spaces more would move the content of the item
        further in than its later lines.
        """
        self.assertMarkdown(
            '<ul><li><ul><li class="oe-nested"><ul><li>b</li></ul></li><li>x</li></ul>Then</li><li>c</li></ul>',
            "- - b\n  - x\n\n  Then\n- c",
        )
        self.assertMarkdown(
            '<ul><li><ul><li class="oe-nested"><ul><hr><li>b</li></ul></li><li>x</li></ul>Then</li></ul>',
            "- * * *\n  - b\n  - x\n\n  Then",
        )
        self.assertMarkdown(
            '<ol><li><ol><li class="oe-nested"><ol start="3"><li>t1</li></ol></li></ol>t2</li><li>t3</li></ol>',
            "1. 3. t1\n\n   t2\n2. t3",
        )

    def test_an_ordered_list_not_from_one_after_loose_text_ending_a_nested_list_is_a_paragraph_apart(self):
        """The nested list starts with a list line but ends in a paragraph, which the list would go on."""
        self.assertMarkdown(
            '<ol start="3"><li class="oe-nested"><ol><li class="oe-nested"><ol><li>x</li>Loose</ol></li></ol></li>'
            "<li>c</li></ol>",
            "  1. x\n\n  Loose\n\n3. c",
        )
        self.assertMarkdown(
            '<ul><li>a<ol><li class="oe-nested"><ol><li>x</li>Loose</ol></li></ol><ol start="3"><li>c</li></ol></li></ul>',
            "- a\n    1. x\n\n    Loose\n\n  3. c",
        )

    def test_a_list_start_is_read_as_html_reads_it(self):
        """Its digits up to the first other character past white space and a sign; CommonMark writes no number
        below 0.
        """
        for start, first_number in (
            ("3px", 3),
            ("\t+4", 4),
            (" 0003 rows", 3),
            ("x3", 1),
            ("-2", 0),
            ("-0", 0),
            ("1234567890", 1),
            ("12345678901", 1),
            ("-12345678901", 1),
        ):
            with self.subTest(start=start):
                self.assertMarkdown(
                    '<ol start="%s"><li>a</li><li>b</li></ol>' % start,
                    "%d. a\n%d. b" % (first_number, first_number + 1),
                )

    def test_an_item_value_sets_its_number_and_the_items_after_it_count_on(self):
        """CommonMark numbers a list on from its first item: an item a browser numbers otherwise opens a list of
        the other delimiter. A value is read as a start is.
        """
        self.assertMarkdown(
            '<ol><li>a</li><li value="5">b</li><li>c</li><li value="2">d</li></ol>', "1. a\n5) b\n6) c\n2. d"
        )
        self.assertMarkdown('<ol><li>a</li><li value="2">b</li></ol>', "1. a\n2. b")
        self.assertMarkdown(
            '<ol><li>a</li><li value="x">b</li><li value="-3">c</li><li value="12345678901">d</li>'
            '<li value=" +7px">e</li></ol><ul><li value="5">f</li></ul>',
            "1. a\n2. b\n0) c\n1) d\n7. e\n\n- f",
        )
        self.assertMarkdown(
            '<ol start="3"><li>a</li><li class="oe-nested"><ol><li>x</li></ol></li><li value="9">b</li></ol>',
            "3. a\n   1. x\n9) b",
        )

    def test_a_reversed_list_counts_down_from_its_start_or_its_item_count(self):
        self.assertMarkdown("<ol reversed><li>a</li><li>b</li><li>c</li></ol>", "3. a\n2) b\n1. c")
        self.assertMarkdown('<ol reversed start="10"><li>a</li><li>b</li></ol>', "10. a\n9) b")
        self.assertMarkdown(
            '<ol reversed><li>a</li><li class="oe-nested"><ol><li>x</li></ol></li><li><ul><li>y</li></ul></li></ol>',
            "2. a\n   1. x\n1) - y",
        )
        self.assertMarkdown(
            '<ol reversed><li>a</li><li value="10">b</li><li>c</li></ol><ol><li>d</li></ol>',
            "3. a\n10) b\n9. c\n\n1) d",
        )
        self.assertMarkdown(
            '<ol reversed start="1"><li>a</li><li>b</li><li>c</li><li value="0">d</li></ol>', "1. a\n0) b\n0. c\n0) d"
        )

    def test_an_empty_ordered_item_shows_its_number_where_an_item_or_nested_lines_follow_it(self):
        """CommonMark would give the items after it other numbers; one ending a part of the list, or an empty
        bullet item, changes no number and is left out.
        """
        for html_content, markdown in (
            ("<ol><li>a</li><li><br></li><li>b</li></ol>", "1. a\n2.\n3. b"),
            ("<ol><li><br></li><li></li><li>b</li></ol>", "1.\n2.\n3. b"),
            ('<ol><li>a</li><li value="7"><br></li><li>b</li></ol>', "1. a\n7)\n8) b"),
            ("<ol reversed><li>a</li><li><br></li><li>b</li></ol>", "3. a\n2)\n1. b"),
            ("<ol><li>a</li><div><li><br></li></div><li>b</li></ol>", "1. a\n2.\n3. b"),
            ("<ol><li><p>a</p><p>b</p></li><li><br></li><li>c</li></ol>", "1. a\n\n   b\n2.\n3. c"),
            ("<ol><li>a</li><p>x</p><li><br></li><li>b</li></ol>", "1. a\n\nx\n\n2.\n3. b"),
            ("<ul><li>a<ol><li>x</li><li><br></li><li>y</li></ol></li></ul>", "- a\n  1. x\n  2.\n  3. y"),
            (
                '<ol><li>a</li><li><br></li><li class="oe-nested"><ol><li>x</li></ol></li><li>b</li></ol>',
                "1. a\n2.\n   1. x\n3. b",
            ),
            ("<ol><li>a</li><li><br></li></ol>", "1. a"),
            ("<ol><li>a</li><li><br></li><p>x</p><li>b</li></ol>", "1. a\n\nx\n\n3. b"),
            ("<ul><li>a</li><li><br></li><li>b</li></ul>", "- a\n- b"),
        ):
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_what_goes_under_an_empty_item_goes_right_under_it(self):
        """A blank line would end the item, which CommonMark lets begin with one blank line at most."""
        self.assertMarkdown(
            '<ol><li>a</li><li><br></li><li class="oe-nested"><ol>Loose<li>x</li></ol></li></ol>',
            "1. a\n2.\n   Loose\n   1. x",
        )
        self.assertMarkdown(
            '<ol><li>a</li><li><br></li><li class="oe-nested"><ol start="3"><li>x</li></ol></li></ol>',
            "1. a\n2.\n   3. x",
        )
        self.assertMarkdown(
            '<ol><li>a</li><li><br></li><li class="oe-nested"><ol><li class="oe-nested"><ol><li>y</li></ol></li>'
            "<li>x</li></ol></li></ol>",
            "1. a\n2.\n   1. y\n   1) x",
        )

    def test_loose_text_right_under_an_empty_item_is_escaped_as_the_text_of_an_item(self):
        """GFM reads a box at the start of the item's text, where the browser shows text."""
        self.assertMarkdown("<ol><li><br></li><ol>[x] loose</ol><li>b</li></ol>", "1.\n   \\[x] loose\n2. b")
        self.assertMarkdown("<ol><li></li><ol><ol>[ ] deep</ol></ol><li>b</li></ol>", "1.\n   \\[ ] deep\n2. b")

    def test_a_list_opening_with_an_empty_item_is_a_paragraph_apart_from_the_text_above_it(self):
        """CommonMark lets no empty item interrupt a paragraph: it would be text of it."""
        self.assertMarkdown("<ul><li>t<ol><li><br></li><li>b</li></ol></li></ul>", "- t\n\n  1.\n  2. b")
        self.assertMarkdown(
            '<ol><li>t</li><li class="oe-nested"><ol><li><br></li><li>b</li></ol></li></ol>', "1. t\n\n   1.\n   2. b"
        )
        self.assertMarkdown(
            '<ol><li class="oe-nested"><ol><li>x</li>Loose</ol></li><li><br></li><li>b</li></ol>',
            "  1. x\n\n  Loose\n\n1.\n2. b",
        )
        self.assertMarkdown(
            "<ul><li>a<ol><li>x</li><p>note</p><li><br></li><li>y</li></ol></li></ul>",
            "- a\n  1. x\n\n  note\n\n  2.\n  3. y",
        )
        self.assertMarkdown(
            '<ul class="o_checklist"><li><ol><li><br></li><li>b</li></ol></li></ul>', "- [ ]\n\n  1.\n  2. b"
        )

    def test_the_list_after_one_ending_under_an_empty_item_takes_the_other_marker(self):
        self.assertMarkdown(
            '<ol><li>a</li><li><br></li><li class="oe-nested"><ul><li>x</li></ul></li></ol><ol><li>b</li></ol>',
            "1. a\n2.\n   - x\n\n1) b",
        )

    def test_numbers_of_ten_digits_or_more_open_no_list(self):
        """CommonMark numbers a list item with nine digits at most: a paragraph so numbered is no list to join or
        to switch the marker of the list after it.
        """
        self.assertMarkdown(
            "<ol><li>a</li></ol><p>1234567890. Released</p><ol><li>b</li></ol>",
            "1. a\n\n1234567890. Released\n\n1. b",
        )
        self.assertMarkdown(
            "<ol><li>a</li></ol><ol><li>b</li></ol><p>1234567890) x</p><ol><li>c</li></ol>",
            "1. a\n\n1) b\n\n1234567890) x\n\n1. c",
        )
        self.assertMarkdown(
            "<ul><li><p>a</p><p>1234567890. Released</p></li></ul>"
            '<ul class="o_checklist"><li>1234567890. Due</li></ul>',
            "- a\n\n  1234567890. Released\n\n+ [ ] 1234567890. Due",
        )
        self.assertMarkdown('<ul class="o_checklist"><li><p>1234567890. z</p></li></ul>', "- [ ] 1234567890. z")

    def test_a_paragraph_of_ten_digits_after_the_list_of_an_item_is_no_text_of_its_last_item(self):
        self.assertMarkdown(
            "<ul><li>a<ol><li>x</li></ol><p>1234567890. y</p></li></ul>", "- a\n  1. x\n\n  1234567890. y"
        )

    def test_the_list_after_one_takes_its_marker_from_its_last_line_of_least_indent(self):
        self.assertMarkdown("<ul><li><p>a</p><p>b</p></li></ul><ul><li>c</li></ul>", "- a\n\n  b\n\n+ c")
        self.assertMarkdown(
            '<ol><li>a</li></ol><ul><li class="oe-nested"><ol><li>b</li></ol></li><li>c</li></ul><ul><li>d</li></ul>',
            "1. a\n\n1) b\n- c\n\n+ d",
        )

    def test_a_checklist_item_opening_with_a_code_block_or_a_quote_has_its_box_alone(self):
        self.assertMarkdown(
            '<ul class="o_checklist"><li class="o_checked"><pre>x = 1</pre>Then</li>'
            "<li><blockquote>Said</blockquote></li><li><code>y</code> typed</li></ul>",
            "- [x]\n\n  ```\n  x = 1\n  ```\n\n  Then\n- [ ]\n\n  > Said\n- [ ] `y` typed",
        )

    def test_a_checklist_item_opening_with_a_heading_a_table_a_rule_or_a_list_has_its_box_alone(self):
        self.assertMarkdown(
            '<ul class="o_checklist"><li><h3>Step</h3>Do it</li>'
            '<li class="o_checked"><table><tr><td>A</td><td>B</td></tr></table></li>'
            "<li><hr>After</li><li><ol><li>one</li></ol>Then</li><li><ul><li>a</li></ul>b</li>"
            "<li># tagged</li><li>1. typed</li></ul>",
            "- [ ]\n\n  ### Step\n\n  Do it\n- [x]\n\n  | A | B |\n  | --- | --- |\n- [ ]\n\n  ---\n\n  After\n"
            "- [ ]\n\n  1. one\n\n  Then\n- [ ]\n\n  - a\n\n  b\n- [ ] \\# tagged\n- [ ] 1\\. typed",
        )

    def test_a_plain_item_starting_like_a_task_list_item_is_escaped(self):
        self.assertMarkdown(
            "<ul><li>[x] not ticked</li><li>[ ] not open</li><li>[X]</li><li>[link] kept</li></ul>"
            '<ol><li>[ ] numbered</li></ol><ul class="o_checklist"><li>[x] typed</li></ul>',
            "- \\[x] not ticked\n- \\[ ] not open\n- \\[X]\n- [link] kept\n\n1. \\[ ] numbered\n\n- [ ] [x] typed",
        )

    def test_x_in_brackets_on_the_line_of_a_box_is_written_as_it_is(self):
        """cmark-gfm ticks a box whose line holds [x] or [X]; the brief is read raw, so the text is kept as written."""
        self.assertMarkdown(
            '<ul class="o_checklist"><li>a [x] b [X] c<br>[x] d</li><li><a href="https://e.com/">X</a> [x]</li>'
            '<li class="o_checked"></li><li class="oe-nested">[X] e</li></ul>',
            "- [ ] a [x] b [X] c\n  [x] d\n- [ ] [X](https://e.com/) [x]\n- [x] [X] e",
        )

    def test_table_is_a_pipe_table(self):
        self.assertMarkdown(
            "<table><thead><tr><th>A</th><th>B|C</th></tr></thead>"
            "<tbody><tr><td><b>1</b></td><td>2<br>3</td></tr><tr><td>only</td></tr></tbody></table>",
            "| A | B\\|C |\n| --- | --- |\n| **1** | 2 3 |\n| only |  |",
        )

    def test_preformatted_text_is_a_fenced_block(self):
        self.assertMarkdown(
            "<pre>def f():\n    return 1<br>```</pre><blockquote><p>said</p><p>twice</p></blockquote><hr>",
            "````\ndef f():\n    return 1\n```\n````\n\n> said\n>\n> twice\n\n---",
        )

    def test_a_line_break_or_a_block_in_code_is_a_space(self):
        """A browser breaks the line, on each side of a block too, which a code span cannot hold: the words on each
        side stay apart.
        """
        self.assertMarkdown(
            "<p><code>[a<br>b]: https://e.com/</code> <code>a<b>x<br>y</b></code> <code>c<div>d</div>e</code></p>",
            "`[a b]: https://e.com/` `ax y` `c d e`",
        )

    def test_what_a_template_a_script_or_a_style_holds_in_code_is_not_written(self):
        self.assertMarkdown(
            "<pre>a<template>t</template>b<script>s()</script><style>p {}</style>c<br>d</pre>"
            "<p><code>e <template>t</template>f<noscript>n</noscript></code></p>",
            "```\nabc\nd\n```\n\n`e f`",
        )

    def test_images(self):
        self.assertMarkdown(
            '<p><img src="/web/image/12?access_token=abc"> <img src="/web/image/13-a1b2c3/name.png">'
            ' <img src="%s"> <img src="https://example.com/a.png" alt="A [1]"> <img alt="none"></p>' % data_uri(RED),
            "![image: shot \\[1\\].png](attachment 12) ![image](attachment 13) %s ![A \\[1\\]](https://example.com/a.png)"
            % NOT_MOVED_IMAGE,
            attachment_names={12: "shot [1].png"},
        )

    def test_linked_attachment_ids(self):
        self.assertEqual(
            get_linked_attachment_ids('<img src="/web/image/12?access_token=a"><a href="/web/image/7">x</a>'), {7, 12}
        )
        self.assertEqual(get_linked_attachment_ids(False), set())

    def test_linked_attachment_ids_in_every_url_form(self):
        self.assertEqual(
            get_linked_attachment_ids(
                '<a href="/web/content/3?download=true&amp;access_token=a"></a>'
                '<img src="https://example.com/web/image/4-ab/x.png"><img src="/web/image?model=ir.attachment&amp;id=5">'
                '<a href="/web/content/ir.attachment/6/datas">x</a><img src="/web/image/res.partner/7/avatar_128">'
                '<img src="/web/image?model=res.partner&amp;id=8"><a href="https://example.com/page/9">y</a>'
            ),
            {3, 4, 5, 6},
        )

    def test_ids_of_more_than_ten_digits_name_no_attachment(self):
        long_id = "9" * 5000
        self.assertEqual(
            get_linked_attachment_ids(
                '<img src="/web/image/%s"><a href="/web/content/%s?download=true">x</a>'
                '<img src="/web/image?model=ir.attachment&amp;id=%s"><img src="/web/image/12">' % ((long_id,) * 3)
            ),
            {12},
        )
        self.assertEqual(
            get_linked_attachment_ids(
                '<img src="/web/image/%s"><img src="/web/image/%s">'
                '<img src="/web/image?model=ir.attachment&amp;id=%s"><img src="/web/image?model=ir.attachment&amp;id=%s">'
                % ("9" * 10, "8" * 11, "7" * 10, "6" * 11)
            ),
            {9999999999, 7777777777},
            "ten digits are read, eleven are not",
        )

    def test_attachment_urls_become_pointers_in_every_form_without_their_token(self):
        cases = {
            '<a href="/web/content/12?unique=ab&amp;download=true&amp;access_token=SECRET" title="spec.pdf"></a>': (
                "[file: real.pdf](attachment 12)"
            ),
            '<a href="/web/content/13?unique=ab&amp;download=true&amp;access_token=SECRET" title="spec [2].pdf"></a>': (
                "[file: spec \\[2\\].pdf](attachment 13)"
            ),
            '<a href="/web/content/13/spec.pdf?access_token=SECRET"></a>': "[file](attachment 13)",
            '<a href="/web/content/13?access_token=SECRET">the spec</a>': "[the spec](attachment 13)",
            '<a href="/web/content/13?access_token=SECRET">/web/content/13?access_token=SECRET</a>': (
                "[file](attachment 13)"
            ),
            '<img src="https://odoo.example.com/web/image/12?access_token=SECRET">': "![image: real.pdf](attachment 12)",
            '<img src="//other.example.com/web/image/13-ab/300x200/x.png?access_token=SECRET" title="shot.png">': (
                "![](//other.example.com/web/image/13-ab/300x200/x.png)"
            ),
            '<img src="//odoo.example.com/web/image/13-ab/300x200/x.png?access_token=SECRET" title="shot.png">': (
                "![image: shot.png](attachment 13)"
            ),
            '<a href="/web/image/14?access_token=SECRET"><img src="/web/image/14?access_token=SECRET" alt="chart"></a>': (
                "[![image: chart](attachment 14)](attachment 14)"
            ),
            '<img src="/web/image?model=ir.attachment&amp;id=15&amp;access_token=SECRET">': "![image](attachment 15)",
            '<img src="/web/content?id=16&amp;access_token=SECRET">': "![image](attachment 16)",
            '<img src="/web/image/ir.attachment/17/datas?access_token=SECRET">': "![image](attachment 17)",
            '<a href="HTTPS://ODOO.example.com:443/web/content/ir.attachment/18/datas/x.pdf?access_token=SECRET">x</a>': (
                "[x](attachment 18)"
            ),
        }
        for html_content, markdown in cases.items():
            with self.subTest(html_content=html_content):
                self.assertMarkdown(
                    "<p>%s</p>" % html_content,
                    markdown,
                    attachment_names={12: "real.pdf"},
                    base_url="https://odoo.example.com",
                )

    def test_attachment_urls_on_another_host_are_outside_urls(self):
        cases = {
            "https://odoo.example.com:8443/web/image/12?access_token=SECRET": (
                "![](https://odoo.example.com:8443/web/image/12)"
            ),
            "https://odoo.example.com.example.net/web/image/12": "![](https://odoo.example.com.example.net/web/image/12)",
            "https://odoo.example.com@other.example.com/web/image/12?access_token=SECRET": (
                "![](https://other.example.com/web/image/12)"
            ),
            "https://[::1/web/image/12": "![](https://[::1/web/image/12)",
            "https://user@odoo.example.com/web/image/12": "![image: real.png](attachment 12)",
            "http://odoo.example.com:80/web/image/12": "![image: real.png](attachment 12)",
        }
        for url, markdown in cases.items():
            with self.subTest(url=url):
                self.assertMarkdown(
                    '<img src="%s">' % url,
                    markdown,
                    attachment_names={12: "real.png"},
                    base_url="https://odoo.example.com",
                )
        self.assertMarkdown(
            '<img src="https://odoo.example.com/web/image/12"> <img src="/web/image/12">',
            "![](https://odoo.example.com/web/image/12) ![image](attachment 12)",
        )

    def test_other_urls_keep_all_but_their_token_parameters(self):
        self.assertMarkdown(
            '<p><a href="https://example.com/my/tasks/5?access_token=SECRET&amp;x=1#frag">task</a>'
            ' <img src="/web/image/res.partner/5/avatar_128?access_token=SECRET" alt="me">'
            ' <a href="https://example.com/s3?X-Amz-Signature=SECRET&amp;X-Amz-Security-Token=SECRET&amp;v=2">file</a>'
            ' <a href="https://example.com/a?access%5Ftoken=SECRET">a</a>'
            " see https://example.com/doc?token=SECRET&amp;page=2 (https://example.com/b#access_token=SECRET)"
            ' <a href="https://example.com/c?api_key=SECRET">https://example.com/c?api_key=SECRET</a>'
            " <code>curl example.com/d?access_token=SECRET</code></p><pre>GET /e?secret=SECRET&amp;q=1</pre>",
            "[task](https://example.com/my/tasks/5?x=1#frag) ![me](/web/image/res.partner/5/avatar_128)"
            " [file](https://example.com/s3?v=2) [a](https://example.com/a)"
            " see https://example.com/doc?page=2 (https://example.com/b) https://example.com/c"
            " `curl example.com/d`\n\n```\nGET /e?q=1\n```",
        )

    def test_text_that_would_open_a_markdown_block_is_escaped(self):
        self.assertMarkdown(
            "<p>## Forged</p><p>&gt; quote<br>- item<br>+ item<br>* item<br>1. one<br>2) two<br>```python<br>~~~<br>"
            "---<br>Title<br>===<br>_ _ _<br>#hashtag stays<br>-5 stays</p><p><code>a``b</code> stays</p>"
            "<ul><li># in an item</li></ul><blockquote><p>- in a quote</p></blockquote>",
            "\\## Forged\n\n\\> quote\n\\- item\n\\+ item\n\\* item\n1\\. one\n2\\) two\n\\```python\n\\~~~\n"
            "\\---\nTitle\n\\===\n\\_ _ _\n#hashtag stays\n-5 stays\n\n```a``b``` stays\n\n"
            "- \\# in an item\n\n> \\- in a quote",
        )

    def test_a_long_fence_and_a_table_written_in_text_are_escaped(self):
        self.assertMarkdown(
            "<p>````<br>`````python<br>````a`b stays<br>| a | b |<br>|---|---|<br>a | b stays<br>--- | ---<br>"
            ":-: | :--<br>a --- | stays</p>",
            "\\````\n\\`````python\n````a`b stays\n\\| a | b |\n\\|---|---|\na | b stays\n\\--- | ---\n"
            "\\:-: | :--\na --- | stays",
        )
        self.assertMarkdown('<ul class="o_checklist"><li>| a | b |</li></ul>', "- [ ] \\| a | b |")

    def test_merged_cells_keep_their_columns_and_the_caption_and_loose_text_stay(self):
        self.assertMarkdown(
            "<table><caption>Sizes</caption>Loose<tbody><tr><th colspan='2'>Wide</th><th rowspan='2'>Tall</th></tr>"
            "<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>-</td><td>d</td></tr></tbody></table>"
            "<table><tr><td>1</td><td rowspan='2'>m</td><td>3</td></tr><tr><td>4</td><td>6</td></tr></table>",
            "Loose\n\nSizes\n\n| Wide |  | Tall |\n| --- | --- | --- |\n| a | b |  |\n| c | - | d |\n\n"
            "| 1 | m | 3 |\n| --- | --- | --- |\n| 4 |  | 6 |",
        )

    def test_content_right_inside_a_list_and_paragraphs_of_an_item_are_kept(self):
        self.assertMarkdown(
            '<ul>Intro<li>a</li>Between <img src="https://example.com/i.png" alt="pic"><li><p>p1</p><p>p2</p></li>'
            "<li>code<pre>x\n\ny</pre></li></ul>",
            "Intro\n\n- a\n\nBetween ![pic](https://example.com/i.png)\n\n- p1\n\n  p2\n- code\n\n  ```\n  x\n\n  y\n  ```",
        )
        self.assertMarkdown("<ul><li>a</li><ul><li>nested</li></ul></ul>", "- a\n  - nested")

    def test_link_labels_are_escaped_and_other_schemes_keep_only_their_text(self):
        self.assertMarkdown(
            '<p><a href="https://example.com">see [1] \\ and <img src="https://example.com/i.png" alt="[i]"></a>'
            ' <a href="javascript:alert(1)">click</a> <a href=" JavaScript:alert(1)">again</a>'
            ' <a href="vbscript:x">old</a> <a href="mailto:dev@example.com">mail</a> <a href="/web#id=5">form</a></p>',
            "[see \\[1\\] \\\\ and ![\\[i\\]](https://example.com/i.png)](https://example.com) click again old"
            " [mail](mailto:dev@example.com) [form](/web#id=5)",
        )

    def test_data_urls_are_not_copied_whatever_their_spelling(self):
        for source in (
            "da&#10;ta:image/png;base64,QUFB",
            "d&#9;ata:image/png;base64,QUFB",
            " DATA:image/png;base64,QUFB",
            "da ta:image/png;base64,QUFB",
        ):
            with self.subTest(source=source):
                self.assertMarkdown(
                    '<p><img src="%s"> <a href="%s">text</a></p>' % (source, source), NOT_MOVED_IMAGE + " text"
                )

    def test_words_that_are_no_urls_keep_their_question_marks_and_hashes(self):
        self.assertMarkdown(
            "<p>Which access token should the agent use? Is the signature required?</p>"
            "<p>Le jeton (token) est-il valide ? Oui, la signature aussi !</p>"
            "<p><code>valid_token?(t)</code> checks the token</p>"
            "<pre># rotate the API token\nexport_tokens()  # is the secret set?\nvalid_token?(t)</pre>",
            "Which access token should the agent use? Is the signature required?\n\n"
            "Le jeton (token) est-il valide ? Oui, la signature aussi !\n\n"
            "`valid_token?(t)` checks the token\n\n"
            "```\n# rotate the API token\nexport_tokens()  # is the secret set?\nvalid_token?(t)\n```",
        )

    def test_urls_keep_an_empty_query_or_fragment_where_no_token_is_dropped(self):
        self.assertMarkdown(
            "<p>The signature request: https://odoo.example.com/web?#id=42&amp;model=sign.request"
            ' <a href="/web?#id=5&amp;model=project.task">the task</a> <a href="https://example.com/faq?">faq</a></p>',
            "The signature request: https://odoo.example.com/web?#id=42&model=sign.request"
            " [the task](/web?#id=5&model=project.task) [faq](https://example.com/faq?)",
        )

    def test_user_passwords_and_portal_signatures_are_not_copied_and_loose_token_pairs_stay_as_written(self):
        html_content = (
            '<p><a href="https://deploy:hunter2@example.com/repo.git">repo</a>'
            ' <a href="https://example.com/my/orders/5?access_token=hunter2&amp;pid=3&amp;hash=hunter2&amp;page=2">'
            'order</a> <a href="/mail/view?model=sale.order&amp;res_id=5&amp;pid=3&amp;hash=hunter2">view</a>'
            ' <a href="/my/orders?pid=3">orders</a></p>'
            "<p>git clone https://oauth2:hunter2@example.com/repo.git</p>"
        )
        markdown = html_to_markdown(html_content)
        self.assertEqual(
            markdown,
            "[repo](https://example.com/repo.git) [order](https://example.com/my/orders/5?page=2)"
            " [view](/mail/view?model=sale.order&res_id=5) [orders](/my/orders?pid=3)\n\n"
            "git clone https://example.com/repo.git",
        )
        self.assertNotIn("hunter2", markdown)
        cases = {
            "<pre>api_key = fields.Char(password=True, groups='base.group_system')</pre>": (
                "```\napi_key = fields.Char(password=True, groups='base.group_system')\n```"
            ),
            "<p><code>def _get_token(self, token=None):</code></p>": "`def _get_token(self, token=None):`",
            "<p>Then with_context(signature=False)</p>": "Then with_context(signature=False)",
            "<p>Call the model with max_tokens=4096</p>": "Call the model with max_tokens=4096",
            "<pre>self.signature=False</pre>": "```\nself.signature=False\n```",
        }
        for html_content, markdown in cases.items():
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_tokens_of_a_query_after_a_valueless_parameter_or_a_bracket_are_not_copied(self):
        cases = {
            "odoo/web/content/5?download&access_token=SECRET": "odoo/web/content/5?download",
            "https://odoo.example.com/web/content/5/a%20(1).pdf?download&access_token=SECRET": (
                "https://odoo.example.com/web/content/5/a%20(1).pdf?download"
            ),
            "https://h/x?x=(1)&access_token=SECRET": "https://h/x?x=(1)",
            "Tom&Jerry=friends, whatever the token": "Tom&Jerry=friends, whatever the token",
        }
        for text, markdown in cases.items():
            with self.subTest(text=text):
                self.assertMarkdown("<p>See %s</p>" % text.replace("&", "&amp;"), "See " + markdown)

    def test_a_token_after_a_bracket_of_the_file_name_is_not_copied(self):
        cases = {
            "https://odoo.example.com/web/image/12-ab12cd34/Screenshot%20(1).png?access_token=SECRET": (
                "https://odoo.example.com/web/image/12-ab12cd34/Screenshot%20(1).png"
            ),
            "/web/content/5/report(1).pdf#access_token=SECRET": "/web/content/5/report(1).pdf",
            "odoo.example.com/web/content/5/a[1].pdf?access_token=SECRET": "odoo.example.com/web/content/5/a[1].pdf",
        }
        for text, markdown in cases.items():
            with self.subTest(text=text):
                self.assertMarkdown("<p>See %s</p>" % text, "See " + markdown)
        # a url that opens no word: markdown link syntax, and a no-break space before it, in code
        code = (
            "[shot (1).png](https://odoo.example.com/web/image/12-ab/shot%20(1).png?access_token=SECRET)\n"
            "x\xa0https://odoo.example.com/web/content/5/a%20(1).pdf?access_token=SECRET"
        )
        self.assertMarkdown(
            "<pre>%s</pre>" % code,
            "```\n%s\n```" % code.replace("?access_token=SECRET", ""),
        )

    def test_a_query_after_a_quote_or_a_bracket_of_code_stays_as_written(self):
        code = (
            'url = base + "?access_token=" + attachment.access_token\n'
            'url = f"{record.url}?access_token={token}"\n'
            "url = '%s?access_token=%s' % (base, token)\n"
            'fetch("x")?token=1\n'
            'foo("a")&token=abc'
        )
        self.assertMarkdown("<pre>%s</pre>" % code.replace("&", "&amp;").replace('"', "&quot;"), "```\n%s\n```" % code)

    def test_a_reference_to_a_token_in_code_stays_as_written(self):
        code = "json.Unmarshal(body, &token)\nrow.Scan(&token, &expiry)\nvoid f(std::string &&token)"
        self.assertMarkdown(
            "<pre>%s</pre><p>Pass &amp;token to the parser</p>" % code.replace("&", "&amp;"),
            "```\n%s\n```\n\nPass &token to the parser" % code,
        )

    def test_a_word_before_a_question_mark_is_no_url_whatever_its_parameter(self):
        self.assertMarkdown(
            "<p>The token goes in mode?sig=1 and key?auth=2</p>", "The token goes in mode?sig=1 and key?auth=2"
        )

    def test_only_an_http_url_of_the_text_loses_its_user(self):
        self.assertMarkdown(
            "<p>//@ts-ignore ssh://git@github.com/org/repo.git //alice@example.com</p>"
            "<pre>//@ts-ignore\ngit clone ssh://git@github.com/org/repo.git</pre>"
            "<p>pip install git+https://oauth2:hunter2@example.com/repo.git</p>",
            "//@ts-ignore ssh://git@github.com/org/repo.git //alice@example.com\n\n"
            "```\n//@ts-ignore\ngit clone ssh://git@github.com/org/repo.git\n```\n\n"
            "pip install git+https://example.com/repo.git",
        )

    def test_text_that_would_open_an_html_block_is_escaped(self):
        self.assertMarkdown(
            "<p>&lt;!-- hidden --&gt;</p><p>&lt;script&gt;run()&lt;/script&gt;</p>"
            "<p>&lt;pre&gt;<br>&lt;/div&gt;<br>&lt;?php</p><p>a &lt;b&gt; stays, 1 &lt; 2 stays</p><p>&lt; 2 stays</p>",
            "\\<!-- hidden -->\n\n\\<script>run()</script>\n\n\\<pre>\n\\</div>\n\\<?php\n\n"
            "a <b> stays, 1 < 2 stays\n\n< 2 stays",
        )

    def test_text_that_would_open_a_link_reference_definition_is_escaped(self):
        """CommonMark shows nothing of a definition, which a paragraph or an item opening with [label]: is, its
        label on more lines too; a table cell or a heading holds none.
        """
        self.assertMarkdown(
            '<p>[x]: http://e.com</p><ol><li>[y]: http://e.com "t"</li></ol><blockquote><p>[z]:<br>http://e.com</p>'
            "</blockquote><p>a<br><br>[w]:<br>http://e.com</p><p>[a<br>b]: http://e.com</p><p>[^1]: note</p>"
            '<ul class="o_checklist"><li>[v]: http://e.com</li></ul><p>[x] stays, and so does [x]: after text</p>'
            "<h3>[h]: stays</h3><table><tr><td>[c]: stays</td></tr></table>",
            '\\[x]: http://e.com\n\n1. \\[y]: http://e.com "t"\n\n> \\[z]:\n> http://e.com\n\na\n\n\\[w]:\nhttp://e.com'
            "\n\n\\[a\nb]: http://e.com\n\n\\[^1]: note\n\n- [ ] \\[v]: http://e.com\n\n"
            "[x] stays, and so does [x]: after text\n\n### [h]: stays\n\n| [c]: stays |\n| --- |",
        )

    def test_a_definition_is_escaped_only_where_a_paragraph_starts(self):
        """An escaped bracket, or a line break after a backslash, is part of a label: escaped further down a
        paragraph, a definition would close a label left open at its start and the paragraph would show nothing. An
        unmarked item's text goes on the paragraph above it, which can close such a label: that label is escaped.
        """
        for html_content, markdown in (
            ("<p>[Draft<br>[1]: https://e.com/spec</p>", "[Draft\n[1]: https://e.com/spec"),
            ("<ol><li>[a<br>[b]: https://e.com/</li></ol>", "1. [a\n   [b]: https://e.com/"),
            ("<p>[a\\[b]: https://e.com/</p>", "\\[a\\[b]: https://e.com/"),
            ("<p>[a\\<br>b]: https://e.com/</p>", "\\[a\\ \nb]: https://e.com/"),
            (
                '<ul><li>[Draft</li><li class="oe-nested">[1]: https://e.com/spec</li></ul>',
                "- \\[Draft\n  \\[1]: https://e.com/spec",
            ),
            (
                '<ul><li>[a</li><li class="oe-nested">b</li><li class="oe-nested">c]: https://e.com/</li></ul>',
                "- \\[a\n  b\n  c]: https://e.com/",
            ),
            (
                '<ul><li>[a\\</li><li class="oe-nested">b]: https://e.com/</li></ul>',
                "- \\[a\\ \n  b]: https://e.com/",
            ),
            (
                '<ul><li>x</li><ul><li>y</li><div>[a</div></ul><li class="oe-nested">b]: https://e.com/</li></ul>',
                "- x\n  - y\n\n  \\[a\n  b]: https://e.com/",
            ),
            ("<ul><li>[a</li><li>b]: https://e.com/</li></ul>", "- [a\n- b]: https://e.com/"),
            (
                '<ul><li>[a</li><li class="oe-nested"><ul><li>b]: https://e.com/</li></ul></li></ul>',
                "- [a\n  - b]: https://e.com/",
            ),
        ):
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_a_backslash_in_text_is_written_as_it_is(self):
        """The brief is read raw, as the [x] of a box line is."""
        self.assertMarkdown(r"<p>C:\path \[draft\]</p>", r"C:\path \[draft\]")

    def test_a_backslash_ending_a_line_shows_before_the_line_break(self):
        """CommonMark reads one as a hard line break and shows no backslash: a space after it keeps it, in a quote, an
        item and before the text of an unmarked item too. Of a run, each pair is one backslash; at the end of a
        paragraph it shows.
        """
        self.assertMarkdown(
            r"<p>a\<br>b\\<br>c\\\<br>d\<br><br>e\</p><blockquote>q\<br>r</blockquote><ol><li>i\<br>j</li></ol>"
            r'<ul><li>k\</li><li class="oe-nested">l</li></ul>',
            "a\\ \nb\\\\\nc\\\\\\ \nd\\\n\ne\\\n\n> q\\ \n> r\n\n1. i\\ \n   j\n\n- k\\ \n  l",
        )

    def test_an_unmarked_item_goes_on_the_paragraph_before_the_children_writing_nothing(self):
        """An empty item or nested list, an item holding a token only, which is left out, or a block holding
        nothing writes nothing: the unmarked item after it goes on the paragraph before it, as one after a list
        goes on its last, and can close a label left open there.
        """
        follow_on = '<li class="oe-nested">see]: https://e.com/spec</li>'
        for empty_child in (
            '<li class="oe-nested"><ul><li><br></li></ul></li>',
            "<p><br></p>",
            "<div></div>",
            "<ul></ul>",
            "<li> </li><div></div>",
            "<li>?token=x&amp;secret=y</li><div></div>",
        ):
            with self.subTest(empty_child=empty_child):
                self.assertMarkdown(
                    "<ul><li>[Draft</li>%s%s</ul>" % (empty_child, follow_on), "- \\[Draft\n  see]: https://e.com/spec"
                )
        for html_content, markdown in (
            (
                '<ul><li>x<ul><li class="oe-nested">[a</li><li></li></ul></li>%s</ul>' % follow_on,
                "- x\n\n  \\[a\n  see]: https://e.com/spec",
            ),
            (
                '<ol><li>x<ol><li class="oe-nested">[a</li><li></li></ol></li>%s</ol>' % follow_on,
                "1. x\n\n   \\[a\n   see]: https://e.com/spec",
            ),
            (
                "<ul><li>x<ul><li>y</li>t</ul>[a</li>%s</ul>" % follow_on,
                "- x\n  - y\n\n  t\n\n  \\[a\n  see]: https://e.com/spec",
            ),
        ):
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_a_label_left_open_before_text_or_at_the_end_of_the_text_stays_as_it_is(self):
        follow_on = '<li class="oe-nested">see]: https://e.com/spec</li>'
        for html_content, markdown in (
            ("<ul><li>[a</li></ul>", "- [a"),
            ("<ul><li>x<ul><li>[a</li></ul></li><li>b</li></ul>", "- x\n  - [a\n- b"),
            ("<ul><li>[a</li><li>b</li><ul></ul>%s</ul>" % follow_on, "- [a\n- b\n  see]: https://e.com/spec"),
            ("<ul><li>[a</li>t%s</ul>" % follow_on, "- [a\n\nt\n\nsee]: https://e.com/spec"),
            (
                "<ul><li>x<ul><li>[a</li>t</ul></li>%s</ul>" % follow_on,
                "- x\n  - [a\n\n  t\n  see]: https://e.com/spec",
            ),
        ):
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_merged_cells_cannot_blow_a_table_up(self):
        markdown = html_to_markdown(
            "<table><tr>%s</tr>%s</table>" % ("<td colspan='1000' rowspan='1000'>x</td>" * 20, "<tr></tr>" * 200)
        )
        rows = markdown.split("\n")
        self.assertEqual(len(rows), 202, "a header, its rule and the 200 rows of the table: a span fills no other")
        self.assertEqual({row.count(" | ") for row in rows}, {99})
        self.assertTrue(rows[0].startswith("| x |  | "), rows[0][:20])
        self.assertTrue(rows[0].endswith(" | %s |" % " ".join(["x"] * 19)), rows[0][-60:])

    def test_a_row_span_runs_to_the_end_of_its_table(self):
        rows_html = "".join("<tr><td>%d</td></tr>" % number for number in range(2, 106))
        markdown = html_to_markdown(
            '<table><tr><td rowspan="200">tall</td><td rowspan="%s">taller</td><td>1</td></tr>%s</table>'
            % ("9" * 5000, rows_html)
        )
        rows = markdown.split("\n")
        self.assertEqual(rows[0], "| tall | taller | 1 |")
        self.assertEqual(rows[2:], ["|  |  | %d |" % number for number in range(2, 106)], "no row shifts a column")

    def test_a_row_span_ends_with_its_row_group(self):
        self.assertMarkdown(
            '<table><thead><tr><th rowspan="50">H</th><th>I</th></tr></thead>'
            "<tbody><tr><td>1</td><td>2</td></tr></tbody><tr><td>3</td><td>4</td></tr></table>",
            "| H | I |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |",
        )
        self.assertMarkdown(
            '<table><tbody><tr><td rowspan="0">A</td><td>1</td></tr><tr><td>2</td></tr></tbody>'
            '<tbody><tr><td>3</td><td>4</td></tr></tbody><tr><td rowspan="00">B</td><td>5</td></tr><tr><td>6</td></tr>'
            "</table>",
            "| A | 1 |\n| --- | --- |\n|  | 2 |\n| 3 | 4 |\n| B | 5 |\n|  | 6 |",
        )

    def test_a_row_or_a_cell_in_a_block_in_a_table_is_written_in_the_table_only(self):
        """A browser moves it out of the block, which keeps the rest, into the table and the row group around it."""
        self.assertMarkdown(
            '<table><tbody><div><tr><td rowspan="2">a</td></tr></div><tr><td>b</td></tr></tbody></table>',
            "| a |  |\n| --- | --- |\n|  | b |",
        )
        self.assertMarkdown(
            "<table><tbody><div>x<tr><td>a</td></tr>y</div><div><span>s<tr><td>b</td></tr></span></div></tbody>"
            "</table><table><tr><div><td>c</td></div><td>d</td></tr></table>"
            "<table><tr><td>e</td><div><tr><td>f</td></tr></div></tr></table>",
            "x\n\ny\n\ns\n\n| a |\n| --- |\n| b |\n\n| c | d |\n| --- | --- |\n\n| e |\n| --- |\n| f |",
        )

    def test_the_outer_spaces_of_the_text_of_a_link_stay_outside_it(self):
        """They keep the link apart from the words around it, those a part of a table in it leaves too."""
        self.assertMarkdown(
            '<p>see<a href="https://e.com/a"> the spec </a>now<a href="https://e.com/b"> </a>or</p>'
            '<table><tr><th>w<a href="https://e.com/c"><tr><td>c</td></tr></a></th></tr></table>',
            "see [the spec](https://e.com/a) now https://e.com/b or\n\n| w https://e.com/c |\n| --- |\n| c |",
        )

    def test_a_link_with_no_text_writes_its_url_apart_from_the_words_around_it(self):
        """A browser shows nothing of it: its url, which the reader may want, is written as a word of its own."""
        self.assertMarkdown(
            '<p>w29<a href="https://e.com/"></a> <a href="https://e.com/a"></a>next, '
            '<a href="https://e.com/b"><template>t</template></a>.</p>'
            '<p><a href="https://e.com/c"></a></p><p>see<a href="/web/content/12"></a>here</p>',
            "w29 https://e.com/ https://e.com/a next, https://e.com/b .\n\nhttps://e.com/c\n\n"
            "see [file](attachment 12) here",
        )

    def test_a_bang_right_before_a_link_is_escaped(self):
        """It would make the link an image, hiding its text, and a file's pointer read as an image's. Before a space,
        other markup or text, and after an odd run of backslashes, which escapes it, of its own text or not, it
        stays as it is.
        """
        for html_content, markdown in (
            ('<p>Wow!<a href="https://e.com/">see</a></p>', "Wow\\![see](https://e.com/)"),
            ('<p><span>so!</span><span><a href="/web/content/12">f</a></span></p>', "so\\![f](attachment 12)"),
            ('<p>r<span>!<a href="https://e.com/">s</a></span></p>', "r\\![s](https://e.com/)"),
            (
                '<p>a!<a href="https://e.com/"> b</a> c!<b>d</b> h!<span>[i](j)</span>'
                ' k!<a href="https://e.com/">https://e.com/</a></p>',
                "a! [b](https://e.com/) c!**d** h![i](j) k!https://e.com/",
            ),
            (
                '<p>e\\!<a href="https://e.com/">g</a> m\\<b></b>!<a href="https://e.com/">n</a></p>',
                "e\\![g](https://e.com/) m\\![n](https://e.com/)",
            ),
            ('<p>t\\<span><span>!<a href="https://e.com/">u</a></span></span></p>', "t\\![u](https://e.com/)"),
            ('<p>p\\<span>\\</span>!<a href="https://e.com/">q</a></p>', "p\\\\\\![q](https://e.com/)"),
            ('<p>t\\<span>\\!<a href="https://e.com/">u</a></span></p>', "t\\\\\\![u](https://e.com/)"),
            ('<p>t\\<span>\\!<a href="/web/content/12">f</a></span></p>', "t\\\\\\![f](attachment 12)"),
            ('<p>x\\<span>\\<span>!<a href="https://e.com/">u</a></span></span></p>', "x\\\\\\![u](https://e.com/)"),
            ('<p>x<a name="n"><span>!<a href="https://e.com/">j</a></span></a></p>', "x\\![j](https://e.com/)"),
            ('<p>t\\<span>\\!<a href="https://e.com/">u!</a></span></p>', "t\\\\\\![u!](https://e.com/)"),
            (
                '<p><span>!<a href="https://e.com/">a</a></span></p><h2><span>!<a href="https://e.com/">b</a></span></h2>'
                '<div>c<b><span>!<a href="https://e.com/">d</a></span></b>'
                ' e<span><div><span>!<a href="https://e.com/">f</a></span></div></span>'
                ' g<a href="https://e.com/g"><span>!<a href="https://e.com/">h</a></span></a>'
                ' i\\<a name="i"><span>!<a href="https://e.com/">j</a></span></a></div>',
                "\\![a](https://e.com/)\n\n## \\![b](https://e.com/)\n\nc**\\![d](https://e.com/)** e \\![f](https://e.com/)"
                " g[\\![h](https://e.com/)](https://e.com/g) i\\![j](https://e.com/)",
            ),
        ):
            with self.subTest(html_content=html_content):
                self.assertMarkdown(html_content, markdown)

    def test_a_backslash_in_a_url_is_encoded(self):
        """CommonMark would read it as escaping the character after it, the closing parenthesis too."""
        self.assertMarkdown(
            '<p><a href="https://e.com/x\\">t</a> <a href="https://e.com/a\\.b">u</a>'
            ' <img src="https://e.com/i\\.png"></p>',
            "[t](https://e.com/x%5C) [u](https://e.com/a%5C.b) ![](https://e.com/i%5C.png)",
        )

    def test_an_ampersand_read_as_a_character_reference_stays_an_ampersand_in_a_url(self):
        """CommonMark reads &amp; in a destination as "&", and a browser reads &reg_id so in the raw href marked
        writes. A url holding one is written in a link, never bare: renderers read it differently there.
        """
        self.assertMarkdown(
            '<p><a href="https://e.com/?a=1&amp;amp;b=2">t</a> <a href="https://e.com/?x&amp;#39;&amp;reg_id=5">u</a>'
            ' <a href="https://e.com/?copy&amp;copy=3&amp;model=x">v</a> <img src="https://e.com/i.png?a&amp;amp;b"></p>'
            '<p><a href="https://e.com/?a&amp;amp;b">https://e.com/?a&amp;amp;b</a> <a href="https://e.com/?c&amp;amp;d"></a>'
            '</p><p><a href="https://e.com/?a&amp;hellip;b">w</a> <a href="https://e.com/?x&amp;copyright=1">c</a>'
            ' <a href="https://e.com/?a&amp;hellip;b">https://e.com/?a&amp;hellip;b</a></p>',
            "[t](https://e.com/?a=1&amp;amp;b=2) [u](https://e.com/?x&amp;#39;&amp;reg_id=5)"
            " [v](https://e.com/?copy&copy=3&model=x) ![](https://e.com/i.png?a&amp;amp;b)\n\n"
            "[https://e.com/?a&amp;amp;b](https://e.com/?a&amp;amp;b) [https://e.com/?c&amp;amp;d](https://e.com/?c&amp;amp;d)\n\n"
            "[w](https://e.com/?a&amp;hellip;b) [c](https://e.com/?x&copyright=1)"
            " [https://e.com/?a&amp;hellip;b](https://e.com/?a&amp;hellip;b)",
        )

    def test_a_backslash_up_to_the_query_of_an_http_or_relative_url_is_a_slash_as_a_browser_reads_it(self):
        self.assertEqual(
            get_linked_attachment_ids(
                '<a href="/web/content/14\\x">a</a><img src="\\web\\image\\15">'
                '<a href="https:\\\\e.com\\web\\content\\16">b</a><img src="/web/image\\?model=ir.attachment&amp;id=17">'
                '<a href="/web/content/18?download=\\x">c</a>'
            ),
            {14, 15, 16, 17, 18},
        )
        self.assertEqual(
            get_linked_attachment_ids('<img src="\\web\\image\\15"><a href="https:\\\\e.com\\web\\content\\16">b</a>'),
            {15, 16},
            "a text naming attachments by backslashes only is read too",
        )
        self.assertMarkdown(
            '<p><a href="/web/content/14\\x">spec</a> <a href="https://odoo.example.com\\web\\content\\15">'
            'https://odoo.example.com\\web\\content\\15</a> <img src="//odoo.example.com\\web\\image\\16" alt="chart"></p>',
            "[spec](attachment 14) [file](attachment 15) ![image: chart](attachment 16)",
            base_url="https://odoo.example.com",
        )

    def test_a_link_whose_text_is_its_url_holding_a_backslash_or_a_bracket_is_an_autolink(self):
        """A word would encode its backslash, and its brackets could make a link with the text around it. An autolink
        opening a line opens no HTML block, but the text's own "<" is escaped there all the same; a relative url makes
        none.
        """
        self.assertMarkdown(
            '<p><a href="https://example.com/a\\b">https://example.com/a\\b</a> and'
            ' <a href="https://e.com/a[1]?access_token=SECRET">https://e.com/a[1]?access_token=SECRET</a>'
            ' [see <a href="https://e.com/x]">https://e.com/x]</a>(note) <a href="https://e.com/y\\"></a>'
            ' <a href="/web/a[1]">/web/a[1]</a></p>'
            '<p><a href="https://e.com/a[1] b">https://e.com/a[1] b</a> opens the line</p>'
            "<p>&lt;https://e.com/&gt; too</p><p>&lt;b&gt; still</p>",
            "<https://example.com/a\\b> and <https://e.com/a[1]> [see <https://e.com/x]>(note) <https://e.com/y\\>"
            " [/web/a\\[1\\]](/web/a[1])\n\n<https://e.com/a[1]%20b> opens the line\n\n\\<https://e.com/> too\n\n\\<b> still",
        )

    def test_a_link_or_an_image_right_after_a_url_of_the_text_is_set_apart_from_it(self):
        """GFM makes an autolink of a url of the text up to a space or a "<", which would take the markup in. After a
        link, an autolink or code, a link needs no space.
        """
        self.assertMarkdown(
            '<p>https://e.com/a<a href="https://e.com/b">x</a> www.e.com/a<a href="/web/content/12">f</a>'
            ' https://e.com/c<img src="https://e.com/i.png"> https://e.com/d<img src="%s">'
            ' https://e.com/e!<a href="https://e.com/b">y</a> https://e.com/f<span><b></b><a href="https://e.com/b">s</a>'
            '</span> <a href="https://e.com/g">https://e.com/g</a><a href="https://e.com/b">z</a></p>'
            '<p><a href="https://e.com/u">x</a><a href="https://e.com/v">y</a>'
            ' <a href="https://e.com/u">see https://e.com/a</a><a href="https://e.com/v">w</a>'
            ' <code>https://e.com/a</code><a href="https://e.com/b">c</a>'
            ' <a href="https://e.com/a[1]">https://e.com/a[1]</a><a href="https://e.com/b">d</a></p>'
            '<p>https://e.com/a<b><a href="https://e.com/b">x</a></b> https://e.com/c<i><img src="https://e.com/i.png"></i>'
            ' https://e.com/d<s><a href="https://e.com/b">s</a></s> https://e.com/e<b><a href="https://e.com/b">y</a> z</b>'
            ' www.e.com<em><a href="https://e.com/b">w</a></em> https://e.com/f<b><i><a href="https://e.com/b">v</a></i></b>'
            ' https://e.com/g<span><b><a href="https://e.com/b">u</a></b></span>'
            ' <a href="https://e.com/u">x</a><b><a href="https://e.com/v">t</a></b></p>' % data_uri(RED),
            "https://e.com/a [x](https://e.com/b) www.e.com/a [f](attachment 12) https://e.com/c ![](https://e.com/i.png)"
            " https://e.com/d \\[image not yet moved to an attachment\\] https://e.com/e! [y](https://e.com/b)"
            " https://e.com/f [s](https://e.com/b) https://e.com/g [z](https://e.com/b)\n\n"
            "[x](https://e.com/u)[y](https://e.com/v) [see https://e.com/a](https://e.com/u)[w](https://e.com/v)"
            " `https://e.com/a`[c](https://e.com/b) <https://e.com/a[1]>[d](https://e.com/b)\n\n"
            "https://e.com/a **[x](https://e.com/b)** https://e.com/c *![](https://e.com/i.png)*"
            " https://e.com/d ~~[s](https://e.com/b)~~ https://e.com/e **[y](https://e.com/b) z**"
            " www.e.com *[w](https://e.com/b)* https://e.com/f ***[v](https://e.com/b)***"
            " https://e.com/g **[u](https://e.com/b)** [x](https://e.com/u)**[t](https://e.com/v)**",
        )

    def test_the_text_s_own_scheme_in_angle_brackets_opening_a_line_is_escaped_and_an_autolink_written_here_not(self):
        """Read on the finished line, the text's own "<TODO:" and a ">" after the markup of a link written here would
        make one autolink of them, losing the link, and "<javascript:...>" a live link in marked.
        """
        self.assertMarkdown(
            '<p>&lt;TODO:<a href="https://e.com/b">spec</a>&gt; then</p><p>&lt;javascript:alert(1)&gt; x</p>'
            '<p>x<br> <a href="https://e.com/a[1] b">https://e.com/a[1] b</a> y</p>'
            '<p><span><a href="https://e.com/c[2]"></a></span> z</p>'
            '<ul class="o_checklist"><li><a href="https://e.com/d[3]">https://e.com/d[3]</a> w</li>'
            "<li>&lt;https://e.com/&gt; v</li></ul>"
            '<p>&lt;javascript:alert(1)&gt; <a href="https://e.com/a[1]">https://e.com/a[1]</a></p>',
            "\\<TODO:[spec](https://e.com/b)> then\n\n\\<javascript:alert(1)> x\n\n"
            "x\n<https://e.com/a[1]%20b> y\n\n<https://e.com/c[2]> z\n\n"
            "- [ ] <https://e.com/d[3]> w\n- [ ] \\<https://e.com/> v\n\n"
            "\\<javascript:alert(1)> <https://e.com/a[1]>",
        )

    def test_an_autolink_written_here_opening_a_line_inside_an_inline_element_is_not_escaped(self):
        """After a line break or a space inside a wrapper, as an editor's colour or emphasis puts one, the line still
        opens with the autolink written here, which an escape would turn into text.
        """
        self.assertMarkdown(
            '<p><font style="color: rgb(255, 0, 0);">Spec:<br>'
            '<a href="https://e.com/list?filter[state]=open">https://e.com/list?filter[state]=open</a></font></p>'
            '<p><span> <a href="https://e.com/a[1]">https://e.com/a[1]</a></span> y</p>'
            '<p><b>See<br><a href="https://e.com/a\\b">https://e.com/a\\b</a></b></p>'
            '<ul><li><span> <a href="https://e.com/b[2]">https://e.com/b[2]</a></span> w</li></ul>'
            '<p>x<a href="https://e.com/c[3]"><br>https://e.com/c[3]</a> v</p>'
            '<h2><span> <a href="https://e.com/d[4]">https://e.com/d[4]</a></span></h2>',
            "Spec:\n<https://e.com/list?filter[state]=open>\n\n<https://e.com/a[1]> y\n\n**See\n<https://e.com/a\\b>**\n\n"
            "- <https://e.com/b[2]> w\n\nx\n<https://e.com/c[3]> v\n\n## <https://e.com/d[4]>",
        )

    def test_a_bare_link_in_another_link_s_text_is_a_word(self):
        """CommonMark lets no link, an autolink neither, stand in another's text: the outer link would be lost. lxml
        keeps a link nested in another through an inline element. It reads as a browser shows its text, its url where
        it has none.
        """
        self.assertMarkdown(
            '<p><a href="https://e.com/o">see <b><a href="https://e.com/?c&amp;amp;d"></a></b> here</a>'
            ' <a href="https://e.com/p">and <b><a href="https://e.com/?a&amp;amp;b">https://e.com/?a&amp;amp;b</a></b></a>'
            ' <a href="https://e.com/q">or <b><a href="https://e.com/x[1]"></a></b></a>'
            ' <a href="https://e.com/r">at <b><a href="https://e.com/a\\b(1)">https://e.com/a\\b(1)</a></b></a></p>',
            "[see **https://e.com/?c&amp;amp;d** here](https://e.com/o) [and **https://e.com/?a&amp;amp;b**](https://e.com/p)"
            " [or **https://e.com/x\\[1\\]**](https://e.com/q) [at **https://e.com/a\\\\b(1)**](https://e.com/r)",
        )

    def test_each_link_of_a_glued_run_reads_back_for_a_url_up_to_the_link_before_it_only(self):
        """Read back to the start of the text each time, a long run of links takes its length squared."""
        read_lengths = []
        get_word_start = html_markdown._get_word_start

        def read_word_start(text, start, end):
            read_lengths.append(end - start)
            return get_word_start(text, start, end)

        with patch.object(html_markdown, "_get_word_start", read_word_start):
            markdown = html_to_markdown("<p>%s%s</p>" % ("x" * 1000, '<a href="https://e.com/">u</a>' * 1000))
        self.assertLessEqual(sum(read_lengths), len(markdown))

    def test_the_pointer_of_an_image_not_yet_moved_is_text_whatever_stands_around_it(self):
        """Its brackets are escaped: a "(" after them would make it a link, a "!" before them an image."""
        image = '<img src="%s">' % data_uri(RED)
        self.assertMarkdown(
            '<p>%s(screenshot) !%s(x) <a href="https://e.com/">%s</a></p><p>%s: below</p>' % ((image,) * 4),
            "\\[image not yet moved to an attachment\\](screenshot) !\\[image not yet moved to an attachment\\](x)"
            " [\\[image not yet moved to an attachment\\]](https://e.com/)\n\n"
            "\\[image not yet moved to an attachment\\]: below",
        )

    def test_a_cell_in_inline_text_preformatted_text_or_code_in_a_table_is_written_in_the_table_only(self):
        """The words around it stay apart, in the heading, preformatted text or code: a limit, as a browser ends that
        at the cell.
        """
        self.assertMarkdown(
            "<table><tr><h3>Total<td>5</td>due</h3></tr></table><table><tr><pre>a<td>b</td>c</pre></tr></table>"
            "<table><tbody><pre>x<tr><td>d</td></tr>y</pre></tbody></table>"
            "<table><tr><td>z</td><div><code>e<td>f</td>g</code></div></tr></table>",
            "### Total due\n\n| 5 |\n| --- |\n\n```\na\nc\n```\n\n| b |\n| --- |\n\n```\nx\n\ny\n```\n\n| d |\n| --- |"
            "\n\n`e g`\n\n| z | f |\n| --- | --- |",
        )

    def test_a_row_in_a_cell_or_the_caption_of_its_table_is_written_as_a_row_only(self):
        """A browser ends the cell or the caption at the row."""
        self.assertMarkdown(
            "<table><tr><td>a<div><tr><div><td>b</td></div></tr></div></td></tr></table>"
            "<table><caption>k<div><tr><div><td>c</td></div></tr></div></caption></table>",
            "| a |\n| --- |\n| b |\n\nk\n\n| c |\n| --- |",
        )

    def test_rows_nested_in_cells_are_written_once_whatever_their_depth(self):
        depth = 12
        markdown = html_to_markdown(
            "<table><tr><td><div>x<tr><td>y" * depth + "</td></tr></div></td></tr></table>" * depth
        )
        self.assertEqual((markdown.count("x"), markdown.count("y")), (depth, depth), "no row written twice")

    def test_the_parts_of_a_table_after_a_table_in_its_cell_are_written_in_the_table_only(self):
        self.assertMarkdown(
            "<table><tr><td><table><tr><td>i</td></tr></table></td></tr><div><tr><div><td>o</td></div></tr></div>"
            "</table>",
            "| \\| i \\| \\| --- \\| |\n| --- |\n| o |",
        )

    def test_cells_outside_a_row_are_in_a_row_a_browser_implies_up_to_the_next_row_or_row_group(self):
        self.assertMarkdown(
            "<table><tbody><td>z</td></tbody></table>"
            "<table><td>a</td><td>b</td><tr><td>c</td></tr><td>d</td><div>x</div><td>e</td></table>"
            "<table><tr><td>f</td></tr><tbody><td>g</td></tbody><div><td>h</td></div></table>"
            "<table><td>i</td><tbody></tbody><td>j</td></table>",
            "| z |\n| --- |\n\nx\n\n| a | b |\n| --- | --- |\n| c |  |\n| d | e |\n\n| f |\n| --- |\n| g |\n| h |\n\n"
            "| i |\n| --- |\n| j |",
        )

    def test_a_caption_anywhere_in_a_table_is_its_caption_ending_the_row_and_the_row_group_it_stands_in(self):
        self.assertMarkdown(
            "<table><tbody><tr><td>a</td><caption>k</caption><td>b</td></tr></tbody></table>"
            "<table><caption>k1</caption><tr><td rowspan='2'>c</td><td>1</td></tr><div><caption>k2</caption></div>"
            "<tr><td>2</td></tr></table>",
            "k\n\n| a |\n| --- |\n| b |\n\nk1\n\nk2\n\n| c | 1 |\n| --- | --- |\n| 2 |  |",
        )

    def test_a_definition_the_blocks_of_a_caption_open_together_is_escaped(self):
        """The caption is one line: a label its first block leaves open, after the markers of a list or a quote too, is
        closed by the blocks after it. A heading holds no definition, and one a block opens itself is escaped once.
        """
        self.assertMarkdown(
            "<table><caption><div>[k</div><div>m]: https://e.com/</div></caption><tr><td>a</td></tr></table>",
            "\\[k m]: https://e.com/\n\n| a |\n| --- |",
        )
        for caption, line in (
            ("<ul><li>[k</li></ul><p>m]: https://e.com/</p>", "- \\[k m]: https://e.com/"),
            ('<ul class="o_checklist"><li>[k</li></ul><p>m]: https://e.com/</p>', "- [ ] \\[k m]: https://e.com/"),
            ("<blockquote><ol><li>[k</li></ol></blockquote><p>m]: https://e.com/</p>", "> 1. \\[k m]: https://e.com/"),
            ("<h3>[k</h3><p>m]: https://e.com/</p>", "### [k m]: https://e.com/"),
            ("<p>[k]: https://e.com/</p><p>m</p>", "\\[k]: https://e.com/ m"),
            ("<p>[k</p><p>m] : https://e.com/</p>", "[k m] : https://e.com/"),
        ):
            with self.subTest(caption=caption):
                self.assertMarkdown("<table><caption>%s</caption></table>" % caption, line)

    def test_a_table_outside_the_cells_of_a_table_ends_it_and_what_follows_is_no_part_of_it(self):
        """A browser closes the table at the start of the other one, which follows it; written as text, in an inline
        element, that one goes before the table.
        """
        self.assertMarkdown(
            "<table><tr><td>a</td></tr><span><table><tr><td>i</td></tr></table></span><tr><td>b</td></tr></table>",
            "i\n\nb\n\n| a |\n| --- |",
        )
        self.assertMarkdown(
            "<table><tr><td>a</td></tr><div>x<table><tr><td>i</td></tr></table>y</div><tr><td>b</td></tr>"
            "<caption>k</caption></table>"
            "<table><tr><td>c</td><div><table><tr><td>j</td></tr></table></div><td>d</td></tr></table>"
            "<table><tbody><div><table><tr><td>l</td></tr></table></div><div><tr><div><td>o</td></div></tr></div>"
            "</tbody></table>",
            "x\n\n| a |\n| --- |\n\n| i |\n| --- |\n\ny\n\nb\n\nk\n\n| c |\n| --- |\n\n| j |\n| --- |\n\nd\n\n"
            "| l |\n| --- |\n\no",
        )

    def test_a_cell_or_a_caption_in_a_list_in_a_table_is_written_in_the_table_only(self):
        """A browser moves it out of the list into the table, with the items it holds."""
        self.assertMarkdown("<table><ul><li>a</li><td>c</td></ul></table>", "- a\n\n| c |\n| --- |")
        self.assertMarkdown("<table><ul><li>a</li><th><ul><li>x</li></ul></th></ul></table>", "- a\n\n| - x |\n| --- |")
        self.assertMarkdown("<table><ul><li>a</li><caption>t<li>x</li></caption></ul></table>", "- a\n\nt x")
        self.assertMarkdown(
            "<table><ol><li>a</li><h2>h<td><li>x</li></td></h2></ol></table>", "1. a\n\n## h\n\n| x |\n| --- |"
        )
        self.assertMarkdown(
            "<table><ol reversed><li>a</li><li>b</li><h2><td><li>x</li></td></h2></ol></table>",
            "2. a\n1) b\n\n| x |\n| --- |",
        )
        self.assertMarkdown(
            "<table><ol><li>a</li><div>t<ul><li>n</li></ul><td><li>x</li></td></div></ol></table>",
            "1. a\n\nt\n\n- n\n\n| x |\n| --- |",
        )

    def test_what_a_template_holds_in_a_table_is_no_part_of_it(self):
        self.assertMarkdown(
            "<table><tr><td>z</td><template><td>a</td></template></tr><template><tr><td>b</td></tr></template></table>",
            "| z |\n| --- |",
        )

    def test_a_span_is_read_as_html_reads_it_up_to_its_first_other_character(self):
        self.assertMarkdown(
            '<table><tr><td colspan="2px">a</td><td>b</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>'
            '<table><tr><td rowspan=" 2 rows">m</td><td>1</td></tr><tr><td>2</td></tr></table>'
            '<table><tr><td colspan="+2">p</td><td>q</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>'
            '<table><tr><td rowspan="-0">z</td><td>1</td></tr><tr><td>2</td></tr><tr><td>3</td></tr></table>',
            "| a |  | b |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n\n| m | 1 |\n| --- | --- |\n|  | 2 |\n\n"
            "| p |  | q |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n\n| z | 1 |\n| --- | --- |\n|  | 2 |\n|  | 3 |",
        )
        self.assertMarkdown(
            '<table><tr><td colspan="\t2">t</td><td colspan="\n2">n</td><td colspan="\f2">f</td>'
            '<td colspan="\r2">r</td><td>e</td></tr></table>',
            "| t |  | n |  | f |  | r |  | e |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        )
        self.assertMarkdown(
            '<table><tr><td colspan="x2">a</td><td colspan="-2">b</td><td colspan="&nbsp;2">c</td>'
            '<td colspan="2">d</td></tr></table>',
            "| a | b | c | d |  |\n| --- | --- | --- | --- | --- |",
        )
        rows = html_to_markdown('<table><tr><td colspan="%spx">x</td></tr></table>' % ("9" * 5000)).split("\n")
        self.assertEqual(rows[0].count(" | "), 99, "the cell spans the most columns a span gives")

    def test_numbers_of_thousands_of_digits_are_bounded(self):
        many = "9" * 5000
        zeros = "0" * 5000
        markdown = html_to_markdown(
            '<table><tr><td colspan="%s" rowspan="%s">x</td></tr></table>'
            '<ol start="%s"><li>a</li></ol><ol start="999999999"><li>b</li><li>c</li></ol>'
            '<table><tr><td colspan="%s2">y</td></tr></table><ol start="%s3"><li>d</li></ol>'
            % (many, many, many, zeros, zeros)
        )
        rows = markdown.split("\n\n")[0].split("\n")
        self.assertEqual(len(rows), 2, "a header and its rule: a span adds no row of its own")
        self.assertEqual(rows[0].count(" | "), 99, "the cell spans the most columns a span gives")
        self.assertIn("\n\n1. a\n\n999999999) b\n999999999) c", markdown, "a list number has nine digits at most")
        self.assertIn("\n\n| y |  |\n| --- | --- |\n\n", markdown, "leading zeros aside, the span is read")
        self.assertIn("\n\n3. d", markdown)


@tagged("post_install", "-at_install")
class TestTaskBrief(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.stage = cls._create_stage("In Progress", 10)
        cls.tags = cls.env["project.tags"].create([{"name": "Brief Backend"}, {"name": "Brief Urgent"}])
        cls._set_develops_this_odoo(cls.project)
        timesheet_user = cls.env.ref("hr_timesheet.group_hr_timesheet_user", raise_if_not_found=False)
        if timesheet_user:
            # With hr_timesheet a read naming no fields reads the task's timesheet lines too.
            cls.user_dev.write({"groups_id": [Command.link(timesheet_user.id)]})
        cls.module = cls.env["ir.module.module"].search([("name", "=", "project")])
        cls.parent_task = cls.Task.create({"name": "Parent of the brief", "project_id": cls.project.id})
        cls.brief_task = (
            cls.Task.with_user(cls.user_lead)
            .create(
                {
                    "name": "Brief  of a task",
                    "project_id": cls.project.id,
                    "parent_id": cls.parent_task.id,
                    "stage_id": cls.stage.id,
                    "task_type": "feature",
                    "planned_hours": 6,
                    "priority_level": "3",
                    "date_deadline": date(2026, 12, 1),
                    "user_ids": [Command.set((cls.user_dev | cls.user_lead).ids)],
                    "resolution": "code",
                    "tag_ids": [Command.set(cls.tags.ids)],
                    "description": DESCRIPTION % data_uri(RED),
                }
            )
            .with_env(cls.env)
        )
        cls.brief_task.module_ids = cls.module
        cls.private_project = cls.env["project.project"].create(
            {"name": "Private Project", "privacy_visibility": "followers"}
        )
        cls.sub_task = cls.Task.create(
            {"name": "Sub-task of the brief", "project_id": cls.project.id, "parent_id": cls.brief_task.id}
        )
        cls.hidden_task = cls.Task.create(
            {"name": "Hidden sub-task", "project_id": cls.private_project.id, "parent_id": cls.brief_task.id}
        )
        cls.blocker_task = cls.Task.create({"name": "Blocker", "project_id": cls.project.id})
        cls.blocked_task = cls.Task.create({"name": "Blocked", "project_id": cls.project.id})
        cls.brief_task.write(
            {
                "depend_on_ids": [Command.set((cls.blocker_task | cls.hidden_task).ids)],
                "dependent_ids": [Command.set(cls.blocked_task.ids)],
            }
        )
        cls.other_task = cls.Task.create(
            {"name": "Elsewhere", "project_id": cls.private_project.id, "module_ids": [Command.set(cls.module.ids)]}
        )
        cls.other_task.message_subscribe(partner_ids=cls.user_dev.partner_id.ids)

    def _brief(self, task=None, user=None, **context):
        task = (task or self.brief_task).with_user(user or self.user_dev).with_context(**context)
        return task.read(["brief"])[0]["brief"]

    def _key(self, task):
        return task.key or "#%d" % task.id

    def _since(self, value):
        return "%s UTC" % fields.Datetime.to_string(value)

    def _post_note(self, body):
        return self.brief_task.with_user(self.user_lead).message_post(body=Markup(body), subtype_xmlid="mail.mt_note")

    def test_header(self):
        task = self.brief_task
        header = get_sections(self._brief())[""]
        self.assertTrue(header.startswith("# %s Brief of a task\n\n" % self._key(task)), header)
        expected_lines = [
            "- Project: AI-driven Project",
            "- Stage: In Progress, since %s" % self._since(task.date_last_stage_update),
            "- Type: Feature",
            "- Estimate: 6 h, size S",
            "- Priority: High",
            "- Deadline: 2026-12-01",
            "- Assignees: Developer, Project Lead",
            "- Author: Project Lead, created %s" % self._since(task.create_date),
            "- Resolution: Code",
            "- Modules: project",
            "- Tags: Brief Backend, Brief Urgent",
            "- Link: %s/web#id=%d&model=project.task&view_type=form" % (task.get_base_url(), task.id),
        ]
        for line in expected_lines:
            self.assertIn("\n%s\n" % line, header + "\n")

    def test_header_shows_the_fields_other_modules_declare(self):
        # a task without sub-tasks: a module may take the planned window of a parent from its sub-tasks
        task = self.blocker_task
        expected_lines = []
        if "planned_date_start" in task._fields and "planned_date_end" in task._fields:
            task.write({"planned_date_start": EARLIER, "planned_date_end": LATER})
            expected_lines.append("- Planned: from %s to %s" % (self._since(EARLIER), self._since(LATER)))
        if "uat_user_ids" in task._fields:
            task.uat_user_ids = self.user_dev
            expected_lines.append("- UAT users: Developer")
        if not expected_lines:
            self.skipTest("no module installed declares a planned window or UAT users")
        header = get_sections(self._brief(task))[""]
        for line in expected_lines:
            self.assertIn("\n%s\n" % line, header + "\n")

    def test_header_shows_the_estimate_coefficient_and_the_actual_hours(self):
        task = self.blocker_task
        task.write({"planned_hours": 3, "estimate_coefficient": 1.2, "actual_hours": 2.5})
        header = get_sections(self._brief(task))[""]
        for line in ("- Estimate: 3 h, size S, coefficient 1.2", "- Actual Hours: 2.5 h"):
            self.assertIn("\n%s\n" % line, header + "\n")

    def test_header_claims_no_coefficient_or_actual_hours_a_task_lacks(self):
        # the 16.0.2.31.0 column init stored a coefficient of 1 on tasks without an Estimate
        unestimated_task = self.Task.create(
            {"name": "Unestimated", "project_id": self.project.id, "estimate_coefficient": 1.0}
        )
        self.assertEqual((unestimated_task.planned_hours, unestimated_task.estimate_coefficient), (0, 1.0))
        estimated_lines = get_sections(self._brief())[""].splitlines()
        self.assertIn("- Estimate: 6 h, size S", estimated_lines)
        self.assertFalse([line for line in estimated_lines if line.startswith("- Actual Hours")], estimated_lines)
        for task in (self.blocker_task, unestimated_task):
            blank_lines = get_sections(self._brief(task))[""].splitlines()
            self.assertFalse(
                [line for line in blank_lines if line.startswith(("- Estimate", "- Actual Hours"))], blank_lines
            )

    def test_header_shows_the_timesheet_hours_apart_from_the_actual_hours(self):
        if "effective_hours" not in self.Task._fields:
            self.skipTest("no module installed declares timesheet hours")
        task = self.blocker_task
        task.actual_hours = 2.5
        self.env.flush_all()
        # a timesheet line would need an employee and an analytic account
        self.env.cr.execute("UPDATE project_task SET effective_hours = 4 WHERE id = %s", (task.id,))
        self.env.invalidate_all()
        header = get_sections(self._brief(task))[""]
        for line in ("- Actual Hours: 2.5 h", "- Timesheet hours: 4 h"):
            self.assertIn("\n%s\n" % line, header + "\n")
        self.assertNotIn("Hours spent", header)

    def test_modules_only_on_a_task_of_the_project_that_develops_this_odoo(self):
        self.assertIn("\n- Modules: project\n", self._brief())
        other_brief = self._brief(self.other_task)
        self.assertEqual(self.other_task.module_ids, self.module)
        self.assertNotIn("Modules", other_brief)

    def test_related_tasks_the_reader_may_read(self):
        self.assertFalse(self.hidden_task.with_user(self.user_dev)._filter_access_rules("read"))
        brief = self._brief()
        self.assertEqual(
            get_sections(brief)["Related tasks"],
            "\n".join(
                [
                    "- Parent: %s Parent of the brief (In Progress)" % self._key(self.parent_task),
                    "- Sub-task: %s Sub-task of the brief (In Progress)" % self._key(self.sub_task),
                    "- Blocked by: %s Blocker (In Progress)" % self._key(self.blocker_task),
                    "- Blocking: %s Blocked (In Progress)" % self._key(self.blocked_task),
                ]
            ),
        )
        self.assertNotIn("Hidden sub-task", brief)

    def test_pull_requests(self):
        pull_requests = self.env["project.pull.request"].create(
            [
                {
                    "url": "https://example.com/owner/brief/pull/1",
                    "state": "merged",
                    "merge_date": EARLIER,
                    "deploy_date": LATER,
                },
                {"url": "https://example.com/owner/brief/pull/2"},
            ]
        )
        self.brief_task.pull_request_ids = pull_requests
        self.assertEqual(
            get_sections(self._brief())["Pull requests"],
            "- https://example.com/owner/brief/pull/1, Merged, merged %s, deployed %s\n"
            "- https://example.com/owner/brief/pull/2, Open" % (self._since(EARLIER), self._since(LATER)),
        )

    def test_description_points_at_the_attachment_of_its_image(self):
        attachment = self.env["ir.attachment"].search(
            [("res_model", "=", "project.task"), ("res_id", "=", self.brief_task.id), ("name", "=", "screen.png")]
        )
        self.assertIn("/web/image/%d?" % attachment.id, self.brief_task.description)
        brief = self._brief()
        self.assertEqual(get_sections(brief)["Description"], EXPECTED_DESCRIPTION % attachment.id)
        self.assertNotIn("/web/image", brief)

    def test_brief_points_at_linked_files_and_copies_no_token(self):
        plan = self.env["ir.attachment"].create(
            {"name": "plan.txt", "raw": b"the plan", "res_model": "project.task", "res_id": self.brief_task.id}
        )
        plan.generate_access_token()
        self.brief_task.solution = (
            '<p>See <a href="/web/content/%d?unique=%s&amp;download=true&amp;access_token=%s" title="old.txt"></a>'
            ' and <a href="https://example.com/my/tasks/%d?access_token=%s">the portal</a></p>'
            % (plan.id, plan.checksum, plan.access_token, self.brief_task.id, plan.access_token)
        )
        brief = self._brief()
        self.assertEqual(
            get_sections(brief)["Solution"],
            "See [file: plan.txt](attachment %d) and [the portal](https://example.com/my/tasks/%d)"
            % (plan.id, self.brief_task.id),
        )
        self.assertNotIn("access_token", brief)
        self.assertNotIn(plan.access_token, brief)

    def test_brief_of_a_text_linking_an_id_of_thousands_of_digits(self):
        long_id = "9" * 5000
        self.brief_task.description = '<p><img src="/web/image/%s"></p>' % long_id
        self.assertEqual(get_sections(self._brief())["Description"], "![](/web/image/%s)" % long_id)

    def test_attachment_urls_point_at_this_database_only_on_its_host(self):
        plan = self.env["ir.attachment"].create(
            {"name": "plan.txt", "raw": b"the plan", "res_model": "project.task", "res_id": self.brief_task.id}
        )
        self.brief_task.solution = (
            '<p><a href="%s/web/content/%d?access_token=x1">here</a>'
            ' <a href="https://other.example.com/web/content/%d?access_token=x2">there</a></p>'
            % (self.brief_task.get_base_url(), plan.id, plan.id)
        )
        self.assertEqual(
            get_sections(self._brief())["Solution"],
            "[here](attachment %d) [there](https://other.example.com/web/content/%d)" % (plan.id, plan.id),
        )

    def test_a_line_break_in_a_name_cannot_open_a_heading(self):
        self.stage.name = "In Progress\n## Forged stage"
        self.env["project.task.session"].create(
            {
                "task_id": self.brief_task.id,
                "session_id": "s-1\n## Forged session",
                "date": EARLIER,
                "user_id": self.user_dev.id,
            }
        )
        brief = self._brief()
        self.assertIn("\n- Stage: In Progress ## Forged stage, since ", brief)
        self.assertEqual(
            get_sections(brief)["Sessions"], "- %s, Developer, session s-1 ## Forged session" % self._since(EARLIER)
        )
        self.assertNotIn("\n## Forged", brief)

    def test_task_text_cannot_forge_a_section_of_the_brief(self):
        self.brief_task.solution = "<p>Plan</p><p>## Attachments</p><p>- forged.txt (attachment 1)</p>"
        self.assertEqual(
            get_sections(self._brief())["Solution"], "Plan\n\n\\## Attachments\n\n\\- forged.txt (attachment 1)"
        )

    def test_image_not_yet_moved_is_a_pointer_without_its_data(self):
        self.env.flush_all()
        solution = '<p>Plan <img src="%s"></p>' % data_uri(RED)
        self.env.cr.execute("UPDATE project_task SET solution = %s WHERE id = %s", [solution, self.brief_task.id])
        self.brief_task.invalidate_recordset(["solution"])
        brief = self._brief()
        self.assertEqual(get_sections(brief)["Solution"], "Plan %s" % NOT_MOVED_IMAGE)
        self.assertNotIn(RED, brief)
        self.assertNotIn("data:", brief)

    def test_questions(self):
        Question = self.env["project.task.question"]
        answered = Question.with_user(self.user_lead).create(
            {
                "task_id": self.brief_task.id,
                "round": "R1",
                "question": "<p>Which <i>layout</i>?</p>",
                "addressed_to_id": self.user_lead.id,
                "answer": "<p>The current one.</p>",
            }
        )
        open_question = Question.create(
            {
                "task_id": self.brief_task.id,
                "round": "R1",
                "question": "<p>Which period?</p>",
                "addressed_to_id": self.user_dev.id,
            }
        )
        self.assertEqual(
            get_sections(self._brief())["Questions and answers"],
            "### Question %d (Answered)\n\n"
            "- Round: R1\n- Addressed to: Project Lead\n- Answered by: Project Lead, on %s\n\n"
            "Question:\n\nWhich *layout*?\n\nAnswer:\n\nThe current one.\n\n"
            "### Question %d (Open)\n\n- Round: R1\n- Addressed to: Developer\n\nQuestion:\n\nWhich period?"
            % (answered.id, self._since(answered.answered_on), open_question.id),
        )

    def test_checklist_progress_leaves_cancelled_items_out(self):
        item_values = {"task_id": self.brief_task.id, "reviewer_id": self.user_lead.id}
        items = self.env["project.task.checklist.item"].create(
            [
                {**item_values, "name": "Write", "user_id": self.user_dev.id, "state": "done"},
                {**item_values, "name": "Test", "user_id": self.user_dev.id, "deadline": LATER},
                {**item_values, "name": "Drop", "user_id": self.user_lead.id, "state": "cancelled"},
            ]
        )
        sections = get_sections(self._brief())
        self.assertEqual(
            sections["Checklist (1 done of 2, cancelled not counted)"],
            "- %d Done: Write (assigned to Developer, reviewer Project Lead)\n"
            "- %d TODO: Test (assigned to Developer, reviewer Project Lead, deadline %s)\n"
            "- %d Cancelled: Drop (assigned to Project Lead, reviewer Project Lead)"
            % (items[0].id, items[1].id, self._since(LATER), items[2].id),
        )

    def test_test_cases_in_their_order_with_their_verification(self):
        Testcase = self.env["project.task.testcase"]
        passed_case = Testcase.with_user(self.user_dev).create(
            {
                "task_id": self.brief_task.id,
                "name": "Brief shows the task",
                "sequence": 20,
                "state": "passed",
                "verification_kind": "automated",
                "test_reference": "/project_ai_driven:TestTaskBrief",
                "steps_to_execute": "<ol><li>Read the brief</li></ol>",
                "expected_results": "<p>The <b>whole</b> task</p>",
            }
        )
        first_case = Testcase.create({"task_id": self.brief_task.id, "name": "Refused elsewhere", "sequence": 10})
        self.assertEqual(
            get_sections(self._brief())["Test cases"],
            "### Test case %d: Refused elsewhere (Not run)\n\n"
            "### Test case %d: Brief shows the task (Passed)\n\n"
            "- Verified by: Developer, on %s, Automated test\n- Test reference: /project_ai_driven:TestTaskBrief\n\n"
            "Steps:\n\n1. Read the brief\n\nExpected results:\n\nThe **whole** task"
            % (first_case.id, passed_case.id, self._since(passed_case.verified_on)),
        )

    def test_test_case_comments_and_the_general_comments(self):
        test_case = self.env["project.task.testcase"].create(
            {"task_id": self.brief_task.id, "name": "Commented", "comments": "<p>Flaky on <b>Mondays</b></p>"}
        )
        self.brief_task.testcase_general_comments = "<p>Run them in order</p>"
        self.assertEqual(
            get_sections(self._brief())["Test cases"],
            "### General comments\n\nRun them in order\n\n"
            "### Test case %d: Commented (Not run)\n\nComments:\n\nFlaky on **Mondays**" % test_case.id,
        )

    def test_sessions_newest_first(self):
        self.env["project.task.session"].create(
            [
                {"task_id": self.brief_task.id, "session_id": "s-1", "date": EARLIER, "user_id": self.user_dev.id},
                {
                    "task_id": self.brief_task.id,
                    "session_id": "s-2",
                    "date": LATER,
                    "user_id": self.user_lead.id,
                    "outcome": "Built the brief",
                },
            ]
        )
        self.assertEqual(
            get_sections(self._brief())["Sessions"],
            "- %s, Project Lead, session s-2: Built the brief\n- %s, Developer, session s-1"
            % (self._since(LATER), self._since(EARLIER)),
        )

    def test_last_ten_internal_notes_oldest_first(self):
        self._flush_tracking()
        notes = [self._post_note("<p>Note %02d</p>" % number) for number in range(1, 11)]
        notes.append(self._post_note("<p><b>Design Decision</b> - Note 11</p>"))
        self._flush_tracking()
        self.brief_task.date_deadline = date(2026, 12, 2)
        self._flush_tracking()
        tracking_message = self.brief_task.message_ids.filtered("tracking_value_ids")[:1]
        self.assertEqual(tracking_message.subtype_id, self.env.ref("mail.mt_note"))
        self.assertGreater(tracking_message.id, notes[-1].id)
        note_section = get_sections(self._brief())["Internal notes (last 10)"]
        expected_blocks = [
            "### %s, Project Lead\n\nNote %02d" % (self._since(note.date), number)
            for number, note in enumerate(notes[1:10], start=2)
        ]
        expected_blocks.append(
            "### %s, Project Lead, Design Decision\n\n**Design Decision** - Note 11" % self._since(notes[-1].date)
        )
        self.assertEqual(note_section, "\n\n".join(expected_blocks))

    def test_note_the_reader_may_not_read_is_left_out(self):
        self._post_note("<p>Shared note</p>")
        self._post_note("<p>Secret note</p>")
        self.env["ir.rule"].create(
            {
                "name": "Project users do not read secret notes",
                "model_id": self.env.ref("mail.model_mail_message").id,
                "groups": [Command.set(self.env.ref("project.group_project_user").ids)],
                "domain_force": "[('body', 'not ilike', 'Secret note')]",
            }
        )
        brief = self._brief()
        self.assertIn("Shared note", brief)
        self.assertNotIn("Secret note", brief)

    def test_attachments(self):
        image = self.env["ir.attachment"].search(
            [("res_model", "=", "project.task"), ("res_id", "=", self.brief_task.id), ("name", "=", "screen.png")]
        )
        plan = self.env["ir.attachment"].create(
            {"name": "plan.txt", "raw": b"the plan", "res_model": "project.task", "res_id": self.brief_task.id}
        )
        self.assertEqual(
            get_sections(self._brief())["Attachments"],
            "- screen.png (attachment %d, image/png, %d bytes)\n- plan.txt (attachment %d, text/plain, 8 bytes)"
            % (image.id, image.file_size, plan.id),
        )

    def test_sections_come_in_order(self):
        self.env["project.task.question"].create({"task_id": self.brief_task.id, "question": "<p>Why?</p>"})
        self.brief_task.solution = "<p>Because.</p>"
        self.env["project.task.checklist.item"].create({"task_id": self.brief_task.id, "name": "Do"})
        self.env["project.task.testcase"].create({"task_id": self.brief_task.id, "name": "Works"})
        self.env["project.task.session"].create({"task_id": self.brief_task.id, "session_id": "s-1"})
        self.brief_task.pull_request_ids = self.env["project.pull.request"].create(
            {"url": "https://example.com/owner/brief/pull/3"}
        )
        self._post_note("<p>Noted</p>")
        titles = list(get_sections(self._brief()))
        self.assertEqual(
            titles,
            [
                "",
                "Related tasks",
                "Pull requests",
                "Description",
                "Questions and answers",
                "Solution",
                "Checklist (0 done of 1, cancelled not counted)",
                "Test cases",
                "Sessions",
                "Internal notes (last 10)",
                "Attachments",
            ],
        )

    def test_reader_who_cannot_read_the_task_is_refused_as_a_read_of_it(self):
        hidden_task = self.hidden_task.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_task.read(["name"])
        with self.assertRaises(AccessError):
            hidden_task.read(["brief"])
        with self.assertRaises(AccessError):
            # assertRaises empties the cache first: the superuser's brief fills it with all a brief reads
            self.assertTrue(self.hidden_task.brief)
            hidden_task.brief  # noqa: B018

    def test_reader_without_rights_on_tasks_is_refused(self):
        employee = self._create_user("Employee", "project_ai_driven_employee", "employee@example.com")
        # an employee keeps the rights on messages and attachments a brief reads
        self.env["ir.model.access"].search(
            [
                ("model_id.model", "=", "project.task"),
                "|",
                ("group_id", "=", False),
                ("group_id", "in", employee.groups_id.ids),
            ]
        ).active = False
        task = self.brief_task.with_user(employee)
        self.assertFalse(task.check_access_rights("read", raise_exception=False))
        with self.assertRaises(AccessError):
            task.read(["brief"])
        with self.assertRaises(AccessError):
            self.assertTrue(self.brief_task.brief)
            task.brief  # noqa: B018

    def test_task_of_a_company_the_reader_has_not_allowed_is_refused_or_left_out(self):
        other_company = self.env["res.company"].create({"name": "Brief Other Company"})
        self.user_dev.company_ids |= other_company
        other_project = self.env["project.project"].create(
            {"name": "Other Company Project", "company_id": other_company.id}
        )
        other_task = self.Task.create({"name": "Task of the other company", "project_id": other_project.id})
        self.brief_task.depend_on_ids |= other_task
        main_company_ids = self.env.company.ids
        self.assertIn(
            "Task of the other company", self._brief(allowed_company_ids=main_company_ids + other_company.ids)
        )
        self.assertNotIn("Task of the other company", self._brief(allowed_company_ids=main_company_ids))
        self.assertTrue(self._brief(other_task, allowed_company_ids=other_company.ids))
        with self.assertRaises(AccessError):
            self.assertTrue(other_task.brief)
            other_task.with_user(self.user_dev).with_context(allowed_company_ids=main_company_ids).brief  # noqa: B018

    def test_brief_follows_the_language_of_the_reader(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.stage.with_context(lang="fr_FR").name = "En cours"
        task = self.brief_task.with_user(self.user_dev)
        self.assertIn("\n- Stage: In Progress, since ", task.with_context(lang="en_US").brief)
        self.assertIn("\n- Stage: En cours, since ", task.with_context(lang="fr_FR").brief)

    def test_read_naming_no_fields_leaves_the_brief_out(self):
        tasks = (self.brief_task | self.sub_task).with_user(self.user_dev)
        task_class = type(self.Task)
        with patch.object(task_class, "_get_brief", autospec=True, side_effect=task_class._get_brief) as get_brief:
            records = tasks.read() + tasks.read([]) + tasks.search_read([("id", "in", tasks.ids)])
            self.assertEqual(len(records), 6)
            self.assertIn("description", records[0])
            self.assertFalse([record for record in records if "brief" in record])
            self.assertEqual(get_brief.call_count, 0)
            briefs = tasks.read(["name", "brief"])
        self.assertEqual(get_brief.call_count, 2)
        self.assertTrue(all(record["brief"] for record in briefs))

    def test_fields_another_module_leaves_out_of_a_read_naming_none_stay_in_the_brief(self):
        task_class = type(self.Task)
        check_field_access_rights = task_class.check_field_access_rights

        def leave_out_the_stage(task, operation, field_names):
            readable_fnames = check_field_access_rights(task, operation, field_names)
            return readable_fnames if field_names else [fname for fname in readable_fnames if fname != "stage_id"]

        with patch.object(task_class, "check_field_access_rights", leave_out_the_stage):
            self.assertNotIn("stage_id", self.brief_task.with_user(self.user_dev).read()[0])
            self.assertIn("\n- Stage: In Progress, since ", self._brief())

    def test_general_comments_the_reader_may_not_read_are_left_out(self):
        self.brief_task.testcase_general_comments = "<p>For managers</p>"
        task_class = type(self.Task)
        general_comments_field = task_class._fields["testcase_general_comments"]
        with (
            patch.object(general_comments_field, "groups", "project.group_project_manager"),
            patch.object(
                task_class,
                "_get_brief_attachment_names",
                autospec=True,
                side_effect=task_class._get_brief_attachment_names,
            ) as get_attachment_names,
        ):
            brief = self._brief()
        self.assertNotIn("For managers", brief)
        texts = get_attachment_names.call_args.args[1]
        self.assertNotIn(self.brief_task.testcase_general_comments, texts)

    def test_brief_is_left_out_of_exports(self):
        self.assertFalse(self.Task.fields_get(["brief"], ["exportable"])["brief"]["exportable"])

    def test_lines_kept_by_their_field_groups_are_left_out_whatever_the_rights_on_their_model(self):
        employee = self._create_user("Employee", "project_ai_driven_employee", "employee@example.com")
        self.env["ir.model.access"].create(
            {
                "name": "Employees read sessions",
                "model_id": self.env.ref("project_ai_driven.model_project_task_session").id,
                "group_id": self.env.ref("base.group_user").id,
                "perm_read": True,
            }
        )
        session = self.env["project.task.session"].create({"task_id": self.brief_task.id, "session_id": "s-1"})
        self.assertTrue(session.with_user(employee).read(["session_id"]))
        # from a warm cache: fetching the lines would check the field's groups on its own
        self.assertIn("Sessions", get_sections(self.brief_task.brief))
        self.assertNotIn("Sessions", get_sections(self._brief(user=employee)))

    def test_lines_kept_from_a_user_outside_the_project_group_are_left_out(self):
        employee = self._create_user("Employee", "project_ai_driven_employee", "employee@example.com")
        self.env["project.task.question"].create({"task_id": self.brief_task.id, "question": "<p>Why?</p>"})
        self.env["project.task.checklist.item"].create({"task_id": self.brief_task.id, "name": "Do"})
        self.env["project.task.testcase"].create({"task_id": self.brief_task.id, "name": "Works"})
        self.env["project.task.session"].create({"task_id": self.brief_task.id, "session_id": "s-1"})
        project_user_sections = get_sections(self._brief())
        self.assertTrue({"Questions and answers", "Test cases", "Sessions"} <= set(project_user_sections))
        sections = get_sections(self._brief(user=employee))
        self.assertIn("Description", sections)
        self.assertFalse(
            [title for title in sections if title.startswith(("Questions", "Checklist", "Test cases", "Sessions"))]
        )
        self.assertNotIn("Modules", sections[""])

    def test_nothing_is_cut(self):
        paragraphs = ["Paragraph %04d %s" % (number, "x" * 100) for number in range(2000)]
        self.brief_task.description = "".join("<p>%s</p>" % paragraph for paragraph in paragraphs)
        description = get_sections(self._brief())["Description"]
        self.assertGreater(len(description), 200 * 1024)
        self.assertEqual(description, "\n\n".join(paragraphs))

    def test_brief_of_several_tasks(self):
        briefs = (self.brief_task | self.sub_task).with_user(self.user_dev).read(["brief"])
        self.assertTrue(briefs[0]["brief"].startswith("# %s Brief of a task\n" % self._key(self.brief_task)))
        self.assertTrue(briefs[1]["brief"].startswith("# %s Sub-task of the brief\n" % self._key(self.sub_task)))

    def _add_lines(self, number):
        task = self.brief_task
        self.env["project.task.question"].create(
            {"task_id": task.id, "question": "<p>Why %d?</p>" % number, "addressed_to_id": self.user_dev.id}
        )
        self.env["project.task.checklist.item"].create(
            {"task_id": task.id, "name": "Do %d" % number, "user_id": self.user_dev.id}
        )
        self.env["project.task.testcase"].create(
            {"task_id": task.id, "name": "Works %d" % number, "steps_to_execute": "<p>Step</p>"}
        )
        self.env["project.task.session"].create(
            {"task_id": task.id, "session_id": "s-%d" % number, "user_id": self.user_lead.id}
        )
        self.Task.create({"name": "Sub-task %d" % number, "project_id": self.project.id, "parent_id": task.id})
        task.pull_request_ids |= self.env["project.pull.request"].create(
            {"url": "https://example.com/owner/brief/pull/%d" % (100 + number)}
        )
        self.env["ir.attachment"].create(
            {"name": "file-%d.txt" % number, "raw": b"x", "res_model": "project.task", "res_id": task.id}
        )
        self._post_note("<p>Note %d</p>" % number)

    def _count_brief_queries(self):
        # a first read fills the caches of groups and rules that flushing the new lines may have cleared
        self.env.flush_all()
        self._brief()
        self.env.invalidate_all()
        query_count = self.env.cr.sql_log_count
        self._brief()
        return self.env.cr.sql_log_count - query_count

    def test_queries_do_not_grow_with_the_lines(self):
        self._add_lines(1)
        query_count = self._count_brief_queries()
        for number in range(2, 6):
            self._add_lines(number)
        self.assertLessEqual(self._count_brief_queries(), query_count)
