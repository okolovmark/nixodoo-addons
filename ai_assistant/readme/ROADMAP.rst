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
* A URL token is recognised as a parameter whose name ends in ``token``
  after ``?``, ``&`` or ``;``, or past a path of ``_url_token_paths``: a
  token parameter right after a URL's ``#``, a percent-encoded one
  (``token%3D``) and a token under another path read as they are.
* Only message text and its copies are masked and redacted: a text that
  code copies from a message into a record's own field, as a task's
  description from the email that created the task, reads as that record's
  other fields do.
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
