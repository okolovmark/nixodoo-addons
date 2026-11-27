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
  label, and its values read *a name on the AI deny list* where a tracked
  link into a model whose names the list may mask shares it. With
  ``mail_tracking``, its tracking values are filtered by field.
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
* In an HTML text, a character or tag ``html2plaintext`` drops inside a
  token's mark splits the mark (a ``\r``, also as a sanitized field stores
  ``&#13;``, a ``\f`` or ``\v``, ``&#0;``, ``&#1;``, a malformed ``&#``,
  ``</td>``, ``<!x>``): the text shown redacts the token, a filter still
  finds its value. Odoo writes no such link.
* A model that searches names in its own code refuses a name filter while
  any of its rows holds a text the tools show otherwise than stored, one
  the employee may not read included; otherwise that code matches the
  stored texts. Its fields are those ``NAME_SEARCH_FIELDS`` declares
  without a context key that widens the search: a product's supplier codes,
  searched with a partner in the context, are not among them. A filter on
  any other text field that is not stored is matched by the field's search
  method, though its value shows redacted.
* A guest's name is masked only in the display names Odoo builds from the
  guest record, by the fields it builds them from, not by their value, and
  in their copies: a group chat that has a name still reads *a name on the
  AI deny list* while a guest is among the members Odoo would name it by.
  A live chat's own name, which Odoo writes from the visitor's, is not
  built from the guest record: it shows, and so do its copies (the record
  name of the messages, ratings and activities on it), the live chat
  report and the messages live chat posts about the visitor. A live chat
  guest is *Visitor* (*Visitor #N* with ``website_livechat``) unless the
  visitor types a name.
* A display name shows a denied field's value where it holds the value's
  string form in any case: a value Odoo writes into the name otherwise, as a
  selection's label or a date or an amount in the employee's language, is
  not found, and the name shows as Odoo builds it.
* A display name is checked for the denied values of its own record and of
  the records it is built from by a link. A text that copies such a value
  or name, other than the copies and texts the list masks (see Copies of a
  name), keeps it: a line's description, a contact's name, which holds its
  company's (a company named by its hidden email shows it in its contacts'
  names), and a message's ``email_from``.
* The copies and texts of a masked name are read for the current display
  name of the record their row names and of its direct links. Kept as
  Odoo stores them: a name a record held before a rename, and the name of
  a record it linked to before (``record_history`` masks the names its
  tracked links held in the texts it shows); a name reached through lines
  or a second link; a name the message's ``preview`` cuts short; a name as
  an alias address spells it, unaccented with dashes for spaces; the text
  of a message's translations and SMS; a calendar event scheduled from an
  activity, named after the activity's record; a file's own name, as a
  printed report's named after its record; and a live chat's own name.
  A ``count_distinct`` of a copy or text counts its stored values.
* A tracked property goes by its label as the definition holds it now: a
  change logged under a property's former name, or of a property since
  removed, matches no hidden or masked label and shows in the message's
  text and in ``record_history``. With ``mail_tracking``, the tracking
  value of such a link reads *a name on the AI deny list*.
* A filter on a copy or text of a name leaves out more rows than the
  display changes, as SQL decides without the names: where the list hides
  a contact's email or VAT number, the rows of every contact with one set,
  its name built from it or not; those of every group chat with a guest
  among its members, named or not; every tracked change of a property's
  link; and every copy
  of a name the list may mask that differs from its record's stored name,
  a renamed record's included. Sorts, groups and aggregates are refused
  over the same rows.
* A sort by a many2one into a model whose names the list masks orders by
  that model's own order, its name among the keys for many models (a
  task's ``project_id`` by the project's sequence and name).
* On a message search that names no model, or more than 5, the refusal of
  a sort by a copy or text counts as the employee the matching messages
  that may show one otherwise, which Odoo checks one by one: where the
  employee reads none of them, it checks each, up to the 10,000 the search
  looks at. How long that takes on a large database is not measured.
* A like filter on a text matches the rows with each ``_``, vertical tab
  and whole placeholder of its value read as any run of characters, and
  checks each row so matched for the marks of a token (``token=``,
  ``pid=``, a path of
  ``_url_token_paths``); it reads every row, or those a trigram index of
  the text finds, for that value with tags between its characters, a
  non-ASCII character of an ilike value as any run of characters too. Then
  the filter takes the tokens' values out of the rows that hold one in
  SQL, taking the inline tags out of a text
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
* A ``=like`` or ``=ilike`` filter on a text that may hold a URL token
  uses no btree index of the text, which can serve Odoo's own ``=like``
  for a value that starts with a fixed text: only a trigram index serves
  its regex across tags. Nor does ``=``, ``!=``, ``in`` or ``not in``
  with a value that holds the placeholder, matched as a ``=like`` of it.
* A vertical tab typed in an ``=`` or ``in`` value without the
  placeholder is compared with the stored text, where a like reads it as
  a token's place. A text that holds the placeholder outside a token, as
  a quoted answer does, is found by no ``=`` with the text as shown, which
  the filter reads with a token's place for each placeholder. A vertical
  tab a text holds reads as a token's place too: the placeholder of a
  filter's value meets it.
* A like value, or an equality value that holds the placeholder, of over
  5,000 characters as a like reads them (a run of wildcards, vertical
  tabs and placeholders, and for an ilike of non-ASCII characters, counts
  once) on a
  text that may hold a URL token is refused:
  PostgreSQL finds a regex across tags of many thousand characters too
  complex.
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
