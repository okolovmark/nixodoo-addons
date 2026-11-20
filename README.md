# nixodoo-addons

Odoo modules that the [nixodoo-copier-template](https://github.com/okolovmark/nixodoo-copier-template)
workflow relies on. A project generated from the template pulls them through its
`addons.yaml`, the same way it pulls an OCA repository.

One branch per Odoo version (`16.0`, `20.0`), modules at the repository root.

| Module | What it gives | Template answer |
|---|---|---|
| `release_notes` | User-facing release notes kept in Odoo: loaded unpublished from the `notes/<key>.html` files of a project module, published by the person who deployed, announced by OdooBot to the users of the affected models | `use_release_notes` |
| `project_ai_driven` | Fields for AI-driven development on a task (type, estimate, priority, technical solution), clarifying questions as rows, test cases and a checklist per task, pull requests as records with a Resolution and a closing rule on the stages a project marks, the agent sessions that worked on a task, the project that develops this Odoo and the installed modules its tasks touch, UAT users on a task, a log of the stage changes of every task, the images of every task's texts kept as attachments of the task, a brief of every task in Markdown that an agent gets in one read, computed for the reader, and keys of projects and their tasks (`PROJ-12`, opened by `/tasks/<key>`) under the field names of OCA `project_key`, whose place it takes | `task_ai_driven` |
| `project_member_access` | A project's Access mode (Standard, Public, Private) and members with a role (Reader, Contributor, Editor, Admin) that decide reads and writes inside Public and Private projects; Standard projects keep Odoo's access | - |
| `project_support_request` | A Support item in the header of the web client that files requests as tasks of the project that develops this Odoo, and lists the user's open ones | - |
| `project_team_dashboard` | A Team Dashboard of workload cards by project, manager or department, a multi-project Gantt chart with dependency lags, baselines and the stage history of tasks, a Backlog list, stage flags for work in progress and backlog, and planned dates of a task that roll up from its sub-tasks; needs no `project_timeline` | - |
| `project_team_dashboard_timeline` | Installs itself beside `project_team_dashboard` and `project_timeline`: the planned dates of `project_timeline` once on the task form and list, read-only on tasks planned from their sub-tasks | - |
| `project_timesheet_systray` | A Timelog item in the header of the web client that starts and stops the timesheet timer of `project_timesheet_time_control` on the user's open tasks | - |
| `web_bundle_refresh_notice` | Odoo's own *The page appears to be out of date.* notice in open backend tabs whose code a module update has made out of date, which Odoo shows only after a version upgrade | - |

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

## Development

- `ruff check` and `ruff format` (config in `ruff.toml`); `pre-commit install` runs both on commit.
- Tests run in CI on the OCA images for the branch's Odoo version (`.github/workflows/test.yml`).
- Module docs follow the OCA layout: fragments in `readme/`, `README.rst` generated from them.

## License

LGPL-3.0-or-later, see [LICENSE](LICENSE), except `project_timesheet_systray`: AGPL-3.0-or-later,
like the OCA module it depends on, see [project_timesheet_systray/LICENSE](project_timesheet_systray/LICENSE).
