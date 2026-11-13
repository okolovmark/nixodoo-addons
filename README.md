# nixodoo-addons

Odoo modules that the [nixodoo-copier-template](https://github.com/okolovmark/nixodoo-copier-template)
workflow relies on. A project generated from the template pulls them through its
`addons.yaml`, the same way it pulls an OCA repository.

One branch per Odoo version (`16.0`, `20.0`), modules at the repository root.

| Module | What it gives | Template answer |
|---|---|---|
| `release_notes` | User-facing release notes kept in Odoo: loaded unpublished from the `notes/<key>.html` files of a project module, published by the person who deployed, announced by OdooBot to the users of the affected models | `use_release_notes` |
| `project_ai_driven` | Fields for AI-driven development on a task (type, estimate, priority, difficulty, clarifying questions and answers, technical solution), test cases and a checklist per task | `task_ai_driven` |

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

## Development

- `ruff check` and `ruff format` (config in `ruff.toml`); `pre-commit install` runs both on commit.
- Tests run in CI on the OCA images for the branch's Odoo version (`.github/workflows/test.yml`).
- Module docs follow the OCA layout: fragments in `readme/`, `README.rst` generated from them.

## License

LGPL-3.0-or-later, see [LICENSE](LICENSE).
