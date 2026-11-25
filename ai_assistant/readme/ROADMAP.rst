* Claude only: when no Claude model answers (an outage, a missing key, the
  workspace's spend limit), the AI Assistant does not answer. There is no
  fallback to another model and no per-employee budget in Odoo, so one heavy user
  can spend the workspace's monthly limit for everyone.
* It reads and never acts: it changes no record and offers no actions.
* An Ask AI answer is seen by everyone who can see the record. Text written
  into a record or an incoming email can steer an answer, but cannot make it
  change anything.
* A shared AI report shows the owner's data, which may be wider than what
  its recipients may read themselves.
* An Ask AI question refused when its answer job runs (the asker left the
  *AI Assistant* group, or Ask AI is no longer available on the record) is
  closed as *Unavailable* without a note on the record: only the asker learns
  of it, from a notification shown while they have Odoo open.
* An AI report page holds no images, SVG, links or fonts of its own, and
  its charts are those of Odoo's Chart.js 4.
* Tracked changes are told apart by their field's label, as a message names
  them: a hidden field's change Odoo logs on another record shows there
  when that record's model tracks a readable field of the same label, or
  has no relational field to or from the hidden field's model; a readable
  field's change is left out where a hidden field of its model shares its
  label. With ``mail_tracking``, its tracking values are filtered by field.
* A bank account's number masks a message by the accounts its record links
  to when the tools run, as stored or sanitized: a mail sent before the
  record's account, or the account's number, changed keeps its text, and
  so does a number written in another form or on a record that does not
  link to its account.
* A URL token is recognised as a parameter whose name ends in ``token``,
  a portal link's ``hash`` beside its ``pid``, or the rest of a URL past a
  path of ``_url_token_paths``: a token under another parameter name or
  path reads as it is, and of several ``hash`` parameters around one
  ``pid`` only the nearest is redacted.
* A model that searches names in its own code refuses a name filter while
  any of its rows holds a text the tools show otherwise than stored, one
  the employee may not read included; otherwise that code matches the
  stored texts. Its fields are those ``NAME_SEARCH_FIELDS`` declares
  without a context key that widens the search: a product's supplier codes,
  searched with a partner in the context, are not among them. A filter on
  any other text field that is not stored is matched by the field's search
  method, though its value shows redacted.
* A guest's name is masked only in the display names Odoo builds from the
  guest record, by the fields it builds them from, not by their value: a
  group chat that has a name still reads *a name on the AI deny list* while
  a guest is among the members Odoo would name it by. Where Odoo copies a
  guest's name as text, the copy shows and a filter matches it: a live
  chat's name, the record name a message, rating or activity shows
  (``record_name``, ``res_name``), the live chat report and the messages
  live chat posts about the visitor. A live chat guest is *Visitor*
  (*Visitor #N* with ``website_livechat``) unless the visitor types a name.
* A like filter on a text checks each row it matches for the marks of a
  token (``token=``, ``pid=``, a path of ``_url_token_paths``), and reads
  every row, or those a trigram index of the text finds, for its value
  with tags between its characters. It checks every row for those marks
  when its value holds a wildcard (``%``, ``_``), a bracket, a non-ASCII
  letter or a part of the placeholder, and so does an equality filter
  whose value holds a token. Then the filter redacts the tokens of the
  rows that hold one in SQL, taking the inline tags out of a text
  wherever a run of URL characters after ``?``, ``&``, ``;``, ``#``,
  ``%``, ``/`` or ``=`` reaches an inline tag, or a ``<`` that one
  follows: in a URL, as a link's visible URL does, and in any other text
  as well, as ``Hello&nbsp;<b>``, ``50%</b>`` or ``and/or<b>``.
* A text filter is slower than Odoo's own. Over 10 KB HTML texts, a word
  took 0.04 ms a row against 0.01 ms for Odoo's own filter; a wildcard
  took 0.13 ms a row that holds a token and 1.0 ms one whose tags it takes
  out, a URL's or such a text's alike. A short or common like value is
  about 10 times slower than Odoo's own, as the token check runs on every
  row it matches. The index finds the rows of a value across tags less
  closely than those of the value: on a table of about 200,000 rows with a
  trigram index, a like takes several to a few tens of milliseconds where
  Odoo's own takes under 5. On a translated text the like uses no trigram
  index, as its condition reads the language's value: it scans the table.
* With PostgreSQL's ``unaccent``, which turns ``«``, ``»``, ``‹``, ``›``
  and a few other signs into ``<`` and ``>``, a like or not like value is
  not found across a tag that holds one of them: in such a row it is
  compared with the stored text, a URL token in it included.
* A search of messages that names no model, or more than 5, looks at the
  first 10,000 matches only, the newest first unless it sorts otherwise;
  link previews are shown for the newest 10,000 messages with a link only.
* Two concurrent presses of *Send new answer* on one question queue one new
  answer, guarded by a row lock on the question and a check read past the
  transaction's snapshot. The tests run in one transaction and do not
  exercise that race.
* ``find_models`` and ``find_menus`` match a word only where a word starts:
  *keeping* does not find *Timekeeping*.
* ``find_models`` and ``find_menus`` do not reduce a word of three letters:
  *OTs* does not find *OT*.
* ``find_models`` and ``find_menus`` form singulars by spelling alone:
  *cookies* does not find *Cookie*, nor *statuses* or *analyses* *Status* or
  *Analysis*.
* ``find_models`` never matches the fields every model has by label, also
  where a model labels one otherwise, such as ``create_date`` as *Creation
  Date*.
* ``do`` is a common word ``find_models`` and ``find_menus`` leave out, also
  where it stands for a delivery order.
