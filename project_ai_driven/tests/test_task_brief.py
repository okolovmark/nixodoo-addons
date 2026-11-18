from datetime import date, datetime
from unittest.mock import patch

from markupsafe import Markup

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

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
            "- Plain\n  - [x] Ticked\n\n- [ ] Box\n  1. one",
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

    def test_merged_cells_cannot_blow_a_table_up(self):
        markdown = html_to_markdown(
            "<table><tr>%s</tr>%s</table>" % ("<td colspan='1000' rowspan='1000'>x</td>" * 20, "<tr></tr>" * 200)
        )
        rows = markdown.split("\n")
        self.assertEqual(len(rows), 101, "a header, its rule and the 99 rows the cells span")
        self.assertEqual({row.count(" | ") for row in rows}, {99})
        self.assertTrue(rows[0].startswith("| x |  | "), rows[0][:20])
        self.assertTrue(rows[0].endswith(" | %s |" % " ".join(["x"] * 19)), rows[0][-60:])


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
