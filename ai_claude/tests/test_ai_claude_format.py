import re

from markupsafe import Markup

from odoo.tests import TransactionCase, tagged

from odoo.addons.ai_claude.models.ai_claude_gateway import answer_from_html, answer_to_html

# Claude's Markdown, as an assistant received it, plus a link.
ANSWER = (
    "**Opening hours:**\n- **Weekdays:** front desk (ground floor)\n- **Saturday:** front desk only"
    "\n\n**What we offer:**\n- Repairs\n- Spare parts, under our own warranty"
    " terms\n\nTo talk to our team, write to us at https://example.com/contactus"
)


@tagged("post_install", "-at_install")
class TestAnswerFormat(TransactionCase):
    def test_answer_to_html_renders_only_a_few_marks(self):
        self.assertEqual(
            answer_to_html("A **bold** and /slanted/ word, `a_field`.\nNext line.\n\n1. First\n2. Second"),
            "<p>A <b>bold</b> and <i>slanted</i> word, <code>a_field</code>.<br/>Next line.</p>"
            "<ol><li>First</li><li>Second</li></ol>",
        )
        self.assertEqual(answer_to_html("Steps:\n- one\n\u2022 two"), "<p>Steps:</p><ul><li>one</li><li>two</li></ul>")
        self.assertEqual(
            answer_to_html('<script>alert("x")</script> <b>typed</b>'),
            "<p>&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt; &lt;b&gt;typed&lt;/b&gt;</p>",
            "what the model types as HTML stays text",
        )
        for text in (
            "Open /web/login first.",
            "The path a/b/c is kept.",
            "See http://example.com/a/b/ too.",
            "Log in at https://example.com/web/login?redirect=/my/ now.",
            "Compute 2*3*4 and 5 * 3 * 2.",
        ):
            self.assertNotIn("<i>", answer_to_html(text), text)
        self.assertEqual(
            answer_to_html("## Offices\nWe are in **Paris** and *Lyon*."),
            "<p><b>Offices</b></p><p>We are in <b>Paris</b> and <i>Lyon</i>.</p>",
        )
        self.assertEqual(answer_to_html("### **Title**"), "<p><b>Title</b></p>")
        self.assertEqual(answer_to_html("(/x/) and **/y/**"), "<p>(<i>x</i>) and <b><i>y</i></b></p>")
        self.assertEqual(answer_to_html(Markup("<b>x</b> **y**")), "<p>&lt;b&gt;x&lt;/b&gt; <b>y</b></p>")

    def test_answer_to_html_keeps_the_models_list_numbers(self):
        self.assertEqual(
            answer_to_html("1. First\n\n2. Second"),
            '<ol><li>First</li></ol><ol start="2"><li>Second</li></ol>',
            "a list split by a blank line must not number every step 1.",
        )
        self.assertEqual(
            answer_to_html("1. Open the form\n   Go to Sales.\n2. Save it"),
            "<ol><li>Open the form<br/>Go to Sales.</li><li>Save it</li></ol>",
            "an indented line stays in its item",
        )
        self.assertEqual(answer_from_html(answer_to_html("1. First\n\n2. Second")), "1. First\n2. Second")

    def test_answer_inline_links_urls_but_not_code_spans(self):
        html = answer_to_html("Apply at https://example.com/jobs or run `curl https://example.com/api`.")
        self.assertIn('href="https://example.com/jobs"', html)
        self.assertIn("<code>curl https://example.com/api</code>", html, "a code span stays text")
        self.assertEqual(html.count("<a "), 1)

    def test_answer_to_html_with_a_base_url_links_only_into_that_odoo(self):
        text = (
            "See https://odoo.test/web#id=5&model=res.partner, /odoo/sales/7. or /web?debug=1 but not"
            " https://exfil.test/?q=1 nor /website nor evil.test/web#x."
        )
        html = answer_to_html(text, base_url="https://odoo.test")
        links = re.findall(r'<a href="([^"]+)" target="_blank" rel="noreferrer noopener">\1</a>', html)
        self.assertEqual(links, ["https://odoo.test/web#id=5&amp;model=res.partner", "/odoo/sales/7", "/web?debug=1"])
        self.assertIn("</a>, ", html, "the punctuation that ends a sentence stays out of the link")
        self.assertIn(" https://exfil.test/?q=1 nor /website nor evil.test/web#x.</p>", html)
        self.assertIn('href="https://exfil.test/?q=1"', answer_to_html(text), "without a base URL every URL is linked")

    def test_answer_from_html_gives_the_marks_back(self):
        self.assertEqual(
            answer_from_html(answer_to_html(ANSWER)),
            ANSWER.replace("\n\n", "\n"),
            "only the blank lines between paragraphs are lost, as html2plaintext always lost them",
        )
        self.assertEqual(answer_from_html(answer_to_html("Steps:\n1. *one*\n2. `two`")), "Steps:\n1. *one*\n2. `two`")
