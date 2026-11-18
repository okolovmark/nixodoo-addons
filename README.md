# nixodoo-addons

Odoo modules that the [nixodoo-copier-template](https://github.com/okolovmark/nixodoo-copier-template)
workflow relies on. A project generated from the template pulls them through its
`addons.yaml`, the same way it pulls an OCA repository.

One branch per Odoo version (`16.0`, `20.0`), modules at the repository root.

| Module | What it gives | Template answer |
|---|---|---|
| `release_notes` | User-facing release notes kept in Odoo: loaded unpublished from the `notes/<key>.html` files of a project module, published by the person who deployed, announced by OdooBot to the users of the affected models | `use_release_notes` |
| `project_ai_driven` | Fields for AI-driven development on a task (type, estimate, priority, technical solution), clarifying questions as rows, test cases and a checklist per task, pull requests as records with a Resolution and a closing rule on the stages a project marks, the agent sessions that worked on a task, the project that develops this Odoo and the installed modules its tasks touch, UAT users on a task, a log of the stage changes of every task, the images of every task's texts kept as attachments of the task, and a brief of every task in Markdown that an agent gets in one read, computed for the reader | `task_ai_driven` |
| `project_member_access` | A project's Access mode (Standard, Public, Private) and members with a role (Reader, Contributor, Editor, Admin) that decide reads and writes inside Public and Private projects; Standard projects keep Odoo's access | - |
| `project_support_request` | A Support item in the header of the web client that files requests as tasks of the project that develops this Odoo, and lists the user's open ones | - |
| `project_timesheet_systray` | A Timelog item in the header of the web client that starts and stops the timesheet timer of `project_timesheet_time_control` on the user's open tasks | - |

## Using a module

In the project's `addons.yaml`:

```yaml
---
ENV:
  DEFAULT_REPO_PATTERN: https://github.com/okolovmark/{}.git
  ODOO_VERSION: 16.0

nixodoo-addons:
  - "release_notes"
  - "project_ai_driven"
```

That tracks the `16.0` branch, which is what the template renders. To pin a commit instead:

```yaml
nixodoo-addons:
  revision: <commit on the 16.0 branch>
  modules:
    - "release_notes"
    - "project_ai_driven"
```

Then `nix run .#update-repos`, and install the module.

### A database that has `project_software` installed

`project_software` is the former name of `project_ai_driven`. The release that renamed it kept `project_software`
as an empty module depending on `project_ai_driven`, so that updating it installed the new module; the release after
it removed the empty module.

- A database updated through the rename release has both installed, `project_software` owning nothing. Uninstall
  `project_software` once no installed module depends on it, and drop it from `addons.yaml`. With its code gone
  and the module still installed, Odoo logs `Some modules are not loaded, some dependencies or manifest may be
  missing` at every start until it is uninstalled.
- A database from before the rename installs `project_ai_driven` (`-i project_ai_driven`) with the server stopped.
  Its `pre_init_hook` takes over every record of `project_software` under its database id and renames the flag
  `is_software_project` to `is_ai_driven`; `project_software` is then empty and is uninstalled as above. A module
  that depends on `project_software` switches its dependency and the flag name in the same deploy.

Everything the upgrade needs is in the *Installation* section of
[`project_ai_driven/README.rst`](project_ai_driven/README.rst).

## Development

- `ruff check` and `ruff format` (config in `ruff.toml`); `pre-commit install` runs both on commit.
- Tests run in CI on the OCA images for the branch's Odoo version (`.github/workflows/test.yml`).
- Module docs follow the OCA layout: fragments in `readme/`, `README.rst` generated from them.

## License

LGPL-3.0-or-later, see [LICENSE](LICENSE), except `project_timesheet_systray`: AGPL-3.0-or-later,
like the OCA module it depends on, see [project_timesheet_systray/LICENSE](project_timesheet_systray/LICENSE).
