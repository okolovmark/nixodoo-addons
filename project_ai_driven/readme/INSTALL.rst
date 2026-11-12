A database that has neither ``project_software`` nor
``kaertech_project_software`` installed needs nothing beyond installing the
module.

**Upgrading from project_software**

``project_software`` remains for one release as an empty module that depends
on this one, so updating it installs this module. Before any data file is
loaded, the ``pre_init_hook`` takes over everything ``project_software``
owns: models, fields, views, access rights, menus and message subtypes keep
their database ids, so checklist items, test cases, field values and
follower subscriptions stay as they are. It renames the flag where the
database stores it: the column, the field on every model that has it, and
the view archs, record rule domains, filters and window actions that name
it.

What the upgrade needs:

* ``addons.yaml`` lists both modules for that release, also when it pins a
  commit.
* The deploy runs an update of ``project_software`` (``-u
  project_software``) with the server stopped: the hook renames the flag's
  column, which running code still reads. A restart without the update
  leaves ``project_software`` and the modules that depend on it unloaded.
* A module that depends on ``project_software`` switches, in the same
  deploy, its dependency and its external id references to
  ``project_ai_driven`` and the flag to ``is_ai_driven``. The hook rewrites
  its stored views; data files with the former name would write it back.
  The view ``project_project_view_form_inherit_software`` is now
  ``project_project_view_form_inherit_ai_driven``.
* Text naming the flag elsewhere (server action code, automated action
  domains, export templates) is not rewritten: change it by hand.
* ``project_software`` is uninstalled, once no installed module depends on
  it, before the release that removes it, and dropped from ``addons.yaml``.

A dependent module that is renamed as well can call three functions of
``odoo.addons.project_ai_driven.hooks`` from its own ``pre_init_hook``:

* ``is_installed(cr, module_name)``: whether a module is installed or marked
  for upgrade.
* ``take_over_module(cr, former, module)``: moves to ``module`` everything
  ``former`` owns: external ids, constraints, many2many tables and the views
  loaded from its files. The caller clears the registry caches.
* ``rename_external_ids(cr, module, renamed_names)``: renames external ids
  of ``module`` from a ``{former name: new name}`` dict.

**Upgrading from kaertech_project_software**

On a database from before the features were cut from
``kaertech_project_software``, where ``project_software`` was never
installed, the hook takes over only what moved here and renames the flag the
same way.
