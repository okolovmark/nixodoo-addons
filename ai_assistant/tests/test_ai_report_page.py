from lxml import html as lxml_html

from odoo.tests import tagged
from odoo.tests.common import BaseCase

from odoo.addons.ai_assistant.utils.report_page import clean_page, page_element_ids
from odoo.addons.ai_assistant.utils.report_spec import ReportSpecError

LAYOUT = (
    "<style>.kpi > b { font-size: 20px }</style>"
    '<h1 id="title">Sales &amp; margin</h1><p class="lead">Revenue rose <b>12%</b>.</p>'
    '<div id="chart"></div><table id="months"><tr><td colspan="2" style="width: 50%">1</td></tr></table>'
    '<a id="months-link">Months</a><p aria-label="note" dir="ltr">Margin &lt; cost</p>'
)
OUTSIDE_HOST = "https://outside.test"
# What reached the frame through the deny-list sanitizer, each a way to make the browser reach a host.
LOADING_PAGES = {
    "leading escaped text": "&lt;link rel=preconnect href=%s&gt; <b>after</b>" % OUTSIDE_HOST,
    "an abruptly closed comment": '<p>a</p><!--><link rel="preconnect" href="%s">-->' % OUTSIDE_HOST,
    "a style in svg": '<svg><style><p></p><link rel="preconnect" href="%s"></style></svg><p>b</p>' % OUTSIDE_HOST,
    "a meta refresh": '<meta http-equiv="refresh" content="0;url=%s"><p>c</p>' % OUTSIDE_HOST,
    "a base": '<base href="%s/"><p>d</p>' % OUTSIDE_HOST,
    "a form": '<form action="%s"><p>e</p><input name="x"></form>' % OUTSIDE_HOST,
    "an image": '<img src="%s/a.png"><p>f</p>' % OUTSIDE_HOST,
    "an external link": '<a href="%s">g</a>' % OUTSIDE_HOST,
    "a link element": '<link rel="preconnect" href="%s"><p>h</p>' % OUTSIDE_HOST,
}


@tagged("post_install", "-at_install")
class TestReportPage(BaseCase):
    def assertFixedPoint(self, clean_html):
        self.assertEqual(clean_page(clean_html)[0], clean_html, "a clean page comes back as it is")

    def test_clean_page_keeps_layout_style_and_text(self):
        clean_html, removed = clean_page(LAYOUT)
        self.assertEqual(removed, {})
        self.assertEqual(clean_html, LAYOUT)
        self.assertFixedPoint(clean_html)

    def test_clean_page_leaves_nothing_that_reaches_a_host(self):
        for case, page in LOADING_PAGES.items():
            with self.subTest(case):
                clean_html, _removed = clean_page(page)
                document = lxml_html.document_fromstring("<html><body>%s</body></html>" % clean_html)
                values = [value for element in document.iter() for value in getattr(element, "attrib", {}).values()]
                self.assertFalse([value for value in values if OUTSIDE_HOST in value], "no attribute names the host")
                for tag in ("<link", "<meta", "<base", "<form", "<img", "<svg", "<!--"):
                    self.assertNotIn(tag, clean_html.lower())
                self.assertFixedPoint(clean_html)
        self.assertEqual(
            clean_page(LOADING_PAGES["leading escaped text"])[0],
            "<p>&lt;link rel=preconnect href=%s&gt; <b>after</b></p>" % OUTSIDE_HOST,
            "text stays text",
        )

    def test_clean_page_names_what_it_removed(self):
        clean_html, removed = clean_page(
            '<p id="intro">Intro</p><script>fetch("x")</script>'
            '<a id="link" href="https://outside.test" onclick="alert(4)">link</a><a href=" #intro">top</a>'
            '<a href=" jav&#x09;ascript:alert(3)">js</a><img src="x" onerror="alert(6)">'
            '<div srcdoc="x" onmouseover="alert(9)" data-x="1">hover</div><noscript><p>no</p></noscript>'
            '<td rowspan="2">cell</td><p colspan="2">not a cell</p>' + "<p>The end, with " + "words " * 40 + "</p>"
        )
        self.assertEqual(
            removed,
            {
                "elements": ["script", "img", "noscript"],
                "attributes": ["href", "onclick", "href", "href", "srcdoc", "onmouseover", "data-x", "colspan"],
            },
        )
        for kept in ('<p id="intro">Intro</p>', '<a id="link">link</a>', "<a>top</a>", "<a>js</a>"):
            self.assertIn(kept, clean_html)
        for kept in ("<div>hover</div>", "<p>no</p>", '<td rowspan="2">cell</td>', "<p>not a cell</p>", "The end"):
            self.assertIn(kept, clean_html, "a tag outside the layout goes, never the text in it")
        self.assertFixedPoint(clean_html)

    def test_clean_page_keeps_what_libxml2_nests_inside_an_embed(self):
        clean_html, removed = clean_page(
            '<object><embed src="x"><script>alert(1)</script><p onclick="x()">Fallback</p></object>'
            '<div id="chart"></div><p>' + "Conclusions. " * 10 + "</p>"
        )
        self.assertTrue(clean_html.startswith('<p>Fallback</p><div id="chart"></div><p>Conclusions.'), clean_html)
        self.assertEqual(removed, {"elements": ["object", "embed", "script"], "attributes": ["onclick"]})

    def test_a_tag_that_goes_never_leaves_a_nesting_the_parser_would_undo(self):
        for page, expected in (
            ("<p><font><div>x</div></font></p>", "<p></p><div>x</div>"),
            ("<a><foo><a>y</a></foo></a>", "<a></a><a>y</a>"),
            ("<td><foo><td>z</td></foo></td>", "<td></td><td>z</td>"),
            ("<p><object><p>w</p></object></p>", "<p></p><p>w</p>"),
        ):
            with self.subTest(page):
                clean_html, _removed = clean_page(page)
                self.assertEqual(clean_html, expected)
                self.assertFixedPoint(clean_html)

    def test_a_style_stays_only_at_the_top_of_the_page(self):
        document = (
            "<html><head><title>Sales</title><style>h1 { color: navy }</style></head>"
            '<body><h1 id="title">Sales</h1><p>Intro<style>p { color: red }</style></p></body></html>'
        )
        clean_html, removed = clean_page(document)
        self.assertEqual(clean_html, '<style>h1 { color: navy }</style><h1 id="title">Sales</h1><p>Intro</p>')
        self.assertEqual(removed, {"elements": ["title", "style"]}, "a style left out is named, never dropped silently")
        self.assertEqual(clean_page("<style>p { content: '<' }</style><p>x</p>")[1], {"elements": ["style"]})
        self.assertFixedPoint(clean_html)

    def test_clean_page_of_pages_with_no_body(self):
        for page in ("<html></html>", " ", "<!-- only a comment -->"):
            with self.subTest(page):
                self.assertEqual(clean_page(page), ("", {}))
        self.assertEqual(clean_page("<form>Loose text</form>")[0], "<span>Loose text</span>")
        self.assertFixedPoint("<span>Loose text</span>")

    def test_clean_page_refuses_a_page_that_is_mostly_script(self):
        with self.assertRaisesRegex(ReportSpecError, "mostly script"):
            clean_page('<div id="chart"></div><script>%s</script>' % ("new Chart();" * 50))
        with self.assertRaisesRegex(ReportSpecError, "mostly script"):
            clean_page('<p onclick="%s">x</p>' % ("a();" * 50))
        with self.assertRaisesRegex(ReportSpecError, "mostly script"):
            clean_page('<img src="x" onerror="%s">' % ("a();" * 50))
        clean_html, removed = clean_page("<p>%s</p><script>x()</script>" % ("Conclusions. " * 10))
        self.assertEqual((removed, "<script" in clean_html), ({"elements": ["script"]}, False))

    def test_every_href_goes_a_fragment_s_too(self):
        # In the app's srcdoc frame "#x" resolves against the app's URL: the frame navigates to an error page.
        clean_html, removed = clean_page('<a href="#total">Total</a><a href="/web">Odoo</a><p id="total">1</p>')
        self.assertEqual(clean_html, '<a>Total</a><a>Odoo</a><p id="total">1</p>')
        self.assertEqual(removed, {"attributes": ["href", "href"]})

    def test_a_style_that_loads_a_file_or_a_font_goes_whole(self):
        page = (
            "<style>@import url(https://outside.test/a.css); body { color: navy }</style>"
            "<style>@font-face { font-family: x; src: local(x) } h1 { color: red }</style>"
            "<style>.x { background-image: -webkit-image-set(\\75 rl(c.png) 1x) }</style>"
            '<p style="color: red; background: u\\72l(https://outside.test/d.png)">Text</p>'
            '<p style="width: expression(alert(1))">IE</p><p style="-moz-binding: url(x.xml#b)">Gecko</p>'
            '<p style="behavior : url(x.htc)">IE again</p><p style="color: blue">Kept</p>'
        )
        clean_html, removed = clean_page(page)
        self.assertEqual(clean_html, '<p>Text</p><p>IE</p><p>Gecko</p><p>IE again</p><p style="color: blue">Kept</p>')
        self.assertEqual(removed["elements"], ["style", "style", "style"])
        self.assertEqual(removed["attributes"], ["style", "style", "style", "style"])
        self.assertEqual(
            sorted(set(removed["css"])),
            ["-moz-binding", "@font-face", "@import", "behavior:", "expression(", "image-set(", "url("],
        )
        self.assertFixedPoint(clean_html)
        clean_html, removed = clean_page('<style>p { color: red }</style><p style="color: red">x</p>')
        self.assertEqual(removed, {}, "CSS without a loader stays as it was written")
        self.assertEqual(clean_html, '<style>p { color: red }</style><p style="color: red">x</p>')

    def test_a_comment_or_an_escape_hides_no_loader(self):
        evil = "https://evil.test/x"
        for case, page in {
            "a comment opened in a string": '<style>a{content:"/*"} b{background:url(%s)} /**/</style><p>x</p>' % evil,
            "the same in a style attribute": """<p style='content:"/*"; background:url(%s) /**/'>x</p>""" % evil,
            "an escaped escape": '<style>@import "x"; a{background:\\5c\\5c 75 rl(%s)}</style><p>x</p>' % evil,
            "an escape behind the comment": '<style>a{content:"/*"} b{background:\\75 rl(%s)} /**/</style><p>x</p>'
            % evil,
            "an escape ended by CR LF": "<style>a{background:u\\72\r\nl(%s)}</style><p>x</p>" % evil,
            "an escape ended by CR LF in a style attribute": '<p style="background:u\\72&#13;&#10;l(%s)">x</p>' % evil,
        }.items():
            with self.subTest(case):
                clean_html, removed = clean_page(page)
                self.assertEqual(clean_html, "<p>x</p>", "the sheet or attribute goes whole")
                self.assertTrue(removed["css"])
                for loader in ("evil.test", "url(", "rl(", "\\"):
                    self.assertNotIn(loader, clean_html)

    def test_an_escaped_control_character_in_a_style_is_never_decoded_into_the_page(self):
        clean_html, removed = clean_page(
            '<style>p::before { content: "\\1" } b { background: url(x.png) }</style><p>x</p>'
        )
        self.assertEqual((clean_html, removed), ("<p>x</p>", {"elements": ["style"], "css": ["url("]}))
        clean_html, removed = clean_page('<style>p::before { content: "\\1 \\0" }</style><p>x</p>')
        self.assertEqual((clean_html, removed), ('<style>p::before { content: "\\1 \\0" }</style><p>x</p>', {}))
        self.assertFixedPoint(clean_html)

    def test_attributes_dropped_on_the_document_and_its_style_are_named(self):
        page = (
            '<html lang="fr" manifest="x.appcache"><head><style media="print" nonce="n">p { color: red }</style></head>'
            '<body onload="x()" class="page"><p>Text</p></body></html>'
        )
        clean_html, removed = clean_page(page)
        self.assertEqual(clean_html, "<style>p { color: red }</style><p>Text</p>")
        self.assertEqual(sorted(removed["attributes"]), ["class", "lang", "manifest", "media", "nonce", "onload"])

    def test_characters_html_does_not_allow_end_as_a_clean_error(self):
        for attribute in ('title="bad\x01value"', 'title="bad\ufffevalue"'):
            clean_html, removed = clean_page("<p %s>Text</p>" % attribute)
            self.assertEqual(clean_html, "<p>Text</p>")
            self.assertEqual(removed, {"attributes": ["title"]}, "an attribute XML cannot hold is dropped")
        self.assertEqual(clean_page("<p>Text\x01 \ufffe</p>"), ("<p>Text </p>", {}), "the parser drops them in text")
        with self.assertRaisesRegex(ReportSpecError, "half of a surrogate pair"):
            clean_page("<p>Text \ud800</p>")

    def test_page_element_ids(self):
        self.assertEqual(page_element_ids(LAYOUT), {"title", "chart", "months", "months-link"})
        self.assertEqual(page_element_ids("Just text"), set())
        self.assertEqual(page_element_ids("<html></html>"), set())
