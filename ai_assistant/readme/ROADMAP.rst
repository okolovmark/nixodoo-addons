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
  its charts are those of Chart.js 2.9.4.
* A URL token is recognised as a parameter whose name ends in ``token``,
  a portal link's ``hash`` beside its ``pid``, or the rest of a URL past a
  path of ``_url_token_paths``: a token under another parameter name or
  path reads as it is, and of several ``hash`` parameters around one
  ``pid`` only the nearest is redacted.
* A like filter on a stored text is decided in the rows whose stored text
  holds each plain run of its value, as Odoo's own filter reads the value;
  those rows are read for the marks of a token (``token=``, ``pid=``, a
  path of ``_url_token_paths``, in an HTML text their character references
  too) and redacted in SQL. A run an inline tag splits in the stored text is
  not found: a value that meets the shown text only across a tag finds
  nothing, since a regex that reads across tags keeps PostgreSQL from
  scanning the table in parallel. On a row whose token's mark an inline tag
  splits, such a value is found where the stored text holds its run
  elsewhere, a token's value included: there a filter tells whether the
  token's value holds a run of the shown text that straddles the tag. A
  value without a run (the placeholder, wildcards alone) reads every row
  for those marks: on a large message table that can
  reach the tools' time limit, and the filter fails with *Narrow it down*.
* In an HTML text, a character or tag ``html2plaintext`` drops inside a
  token's mark (``\r``, which the sanitizer writes for ``&#13;``, ``\f``,
  ``\v``, ``&#0;``, ``&#1;``, ``</td>``) splits the mark for a filter,
  which then matches the token's value; the text shown is redacted.
* A filter value that holds the placeholder meets a token's value only: a
  text that holds the placeholder itself, as an AI answer quoting a
  redacted text does, is not found by it.
* A message keeps its text where it writes a Discuss meeting's URL with
  character references: only the URL's token is redacted.
* A company-dependent text (a field Odoo 16 keeps in ``ir.property``, per
  company) shows its tokens redacted, but a filter on it compares the
  stored value, token included.
* An analytic distribution filtered by an account's name goes by Odoo's
  name search of the accounts, which compares their stored name and code:
  it is refused while either is denied, or holds in any account a text the
  tools show otherwise.
* A message keeps its text where it shows a bank account's number without
  linking to the account's record, as a mail written by hand may.
* A model that searches names in its own code refuses a name filter while
  any of its rows holds a text the tools show otherwise than stored, one
  the employee may not read included; otherwise that code matches the
  stored texts. Its fields are those ``NAME_SEARCH_FIELDS`` declares
  without a context key that widens the search: a product's supplier
  codes, searched with a partner in the context, are not among them. A
  module that adds such a search declares it through
  ``_name_search_fields``, or the tools refuse a name filter on its model.
  A filter on any other text field that is not stored is matched by the
  field's search method, though its value shows redacted.
* A display name built from a denied record or field is masked by the
  fields ``_rec_name`` and ``NAME_GET_SOURCES`` name, not by its value:
  where Odoo copies such a name as text (the record name a message or
  activity shows, the old and new value of a link's tracked change, a live
  chat's name), the copy shows and a filter matches it.
* Not in this version, as Odoo 16 has no such data: scheduled messages,
  message translations, the attendance kiosk's URL, shared dashboards,
  product feeds, ``html_field_history`` and a live chat's member history.
  Odoo 16 keeps one link preview per URL, each of one message, so no
  preview is left out past a number of messages, and it logs each tracked change by its field,
  so none is hidden by its label.
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
