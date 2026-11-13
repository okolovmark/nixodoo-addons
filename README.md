# nixodoo-addons

Odoo modules that the [nixodoo-copier-template](https://github.com/okolovmark/nixodoo-copier-template)
workflow relies on. A project generated from the template pulls them through its
`addons.yaml`, the same way it pulls an OCA repository.

One branch per Odoo version (`16.0`, `20.0`), modules at the repository root.

| Module | What it gives | Template answer |
|---|---|---|
| `release_notes` | User-facing release notes kept in Odoo: loaded unpublished from the `notes/<key>.html` files of a project module, published by the person who deployed, announced by OdooBot to the users of the affected models | `use_release_notes` |
| `project_ai_driven` | Fields for AI-driven development on a task (type, estimate, priority, difficulty, clarifying questions and answers, technical solution), test cases and a checklist per task, pull requests as records with a Resolution and a closing rule on the stages a project marks, a log of the stage changes of every task | `task_ai_driven` |

## Using a module

In the project's `addons.yaml`:

```yaml
---
ENV:
  DEFAULT_REPO_PATTERN: https://github.com/okolovmark/{}.git
  ODOO_VERSION: 20.0

nixodoo-addons:
  - "release_notes"
  - "project_ai_driven"
```

That tracks the `20.0` branch, which is what the template renders. To pin a commit instead:

```yaml
nixodoo-addons:
  revision: <commit on the 20.0 branch>
  modules:
    - "release_notes"
    - "project_ai_driven"
```

Then `nix run .#update-repos`, and install the module.

### A database that has `project_software` installed

`project_software` is the former name of `project_ai_driven`. Such a database is not updated in place. List
`project_ai_driven` in `addons.yaml` in place of `project_software`, run `nix run .#update-repos`, install
`project_ai_driven`, then uninstall `project_software`. The tasks keep their field values, checklists and test
cases. The project flag does not come along: tick **AI-driven Project** again on each project. Between the two
steps every page the module adds to the task form shows twice, and each start logs that `project_software` cannot
be loaded.

The record of the rename is
[`docs/adr/0001-project-software-becomes-project-ai-driven-by-a-plain-rename.md`](docs/adr/0001-project-software-becomes-project-ai-driven-by-a-plain-rename.md);
the steps are also in the *Installation* section of [`project_ai_driven/README.rst`](project_ai_driven/README.rst).

## Development

- `ruff check` and `ruff format` (config in `ruff.toml`); `pre-commit install` runs both on commit.
- Tests run in CI on the OCA images for the branch's Odoo version (`.github/workflows/test.yml`).
- Module docs follow the OCA layout: fragments in `readme/`, `README.rst` generated from them.

## License

LGPL-3.0-or-later, see [LICENSE](LICENSE).
