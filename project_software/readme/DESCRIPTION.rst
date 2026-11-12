Transitional module: ``project_software`` was renamed to ``project_ai_driven``
and holds nothing itself any more. It only depends on ``project_ai_driven``,
so that updating a database that has ``project_software`` installed installs
the new module, which takes over every record under its database id. Uninstall
it once the database is updated and no installed module depends on it: it is
removed in the next release.
