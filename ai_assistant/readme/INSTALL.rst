Installing needs ``ai_claude`` and the ``anthropic`` and ``httpx2`` Python
packages; give the job channel a capacity (see Configuration).

**A database that holds the AI Assistant under another module**

The AI Assistant may be installed already under another module, which then
hands it over: that module drops it from its code and depends on this one,
and its update installs this module first. Update it with the server
stopped. ``ai_claude`` installs in the same update and adopts its own
records first (see its README).

* Before any data file is loaded, the ``pre_init_hook`` takes the records
  that module holds under this module's external ids: the models, their
  fields and selection values (the *Ask AI* and *AI Assistant* values of the
  conversation's *Assistant*, with their conversations), the group, access
  rights and record rules, settings, the *Ask AI* subtype, the *AI
  Assistant* partner, the job channel and job functions, the crons that
  close stale questions and send the weekly digest, the xlsx report, views,
  actions and menus, with the constraints and many2many tables of its
  models. Every record keeps its database id: conversations, answers, tool
  calls, AI reports, their shares and share logs stay. A holder is a module
  that holds the external id of a model of this module; one that held none
  of the records of its data files keeps its settings.
* The holder's settings, ``<holder>.<name>``, become ``ai_assistant.<name>``
  with their values, unless that key exists already. Each setting of this
  module's data file whose key has a row takes its external id on that row,
  so a setting emptied and filled again keeps its value.
* The data files loaded at install write even noupdate records: the
  ``post_init_hook`` writes back the values the adopted noupdate records had,
  so settings, crons and the job channel stay as they were, and removes again
  a ``forcecreate="0"`` setting the holder no longer had (an emptied one),
  which the data file creates at install.
* The holder's xlsx report model, ``report.<holder>.ai_report_xlsx``, becomes
  ``report.ai_assistant.ai_report_xlsx``, with its fields, under the same
  ids, unless that model exists already.
* The cron *AI Assistant: watch for Claude going quiet* is this module's
  own and is created at install.
* A database where no other module holds a model of this module is left
  untouched, and a second run finds nothing to move.
