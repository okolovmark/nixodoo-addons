Installing needs the ``anthropic`` and ``httpx2`` Python packages. A server
whose environment lost ``anthropic`` after the install still loads; the
chain then asks no model and records the attempt *config*.

**A database that holds these records under another module**

The Claude layer may be installed already under another module, which then
hands it over: that module drops it from its code and depends on this one,
and its update installs this module first. Update it with the server
stopped.

* Before any data file is loaded, the ``pre_init_hook`` takes from the
  module that holds the external id of ``ai.exchange.mixin`` the external
  ids of this module: the models ``ai.conversation`` and
  ``ai.exchange.mixin``, the fields and selection values their classes
  declare here, the access right, the three views (read from this module's
  files from then on), the *Conversations* action, and the foreign keys of
  the conversation's ``create_uid`` and ``write_uid``. Every record keeps its
  database id, and so does every conversation.
* What the holder declares on the conversation itself, an assistant value,
  its own fields and their foreign keys, stays with it. So does an external
  id this module holds already.
* A database where no other module holds the external id of
  ``ai.exchange.mixin`` is left untouched, and a second run finds nothing to
  move.
