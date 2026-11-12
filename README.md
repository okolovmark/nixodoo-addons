# nixodoo-addons

Odoo modules that the [nixodoo-copier-template](https://github.com/okolovmark/nixodoo-copier-template)
workflow relies on. A project generated from the template pulls them through its
`addons.yaml`, the same way it pulls an OCA repository.

One branch per Odoo version (`16.0`, `20.0`), modules at the repository root.

| Module | What it gives | Template answer |
|---|---|---|
| `release_notes` | User-facing release notes kept in Odoo: loaded unpublished from the `notes/<key>.html` files of a project module, published by the person who deployed, announced by OdooBot to the users of the affected models | `use_release_notes` |
| `project_ai_driven` | Fields for AI-driven development on a task (type, estimate, priority, difficulty, clarifying questions and answers, technical solution), test cases and a checklist per task | `task_ai_driven` |
| `project_software` | Transitional: the former name of `project_ai_driven`, an empty module that depends on it. Removed in the next release | - |

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

`project_software` was renamed to `project_ai_driven`. For this one release such a project lists both, also when
it pins a commit:

```yaml
nixodoo-addons:
  - "release_notes"
  - "project_ai_driven"
  - "project_software"
```

Then `nix run .#update-repos` and update `project_software` (`-u project_software`) with the server stopped; a
restart without the update leaves it and the modules that depend on it unloaded. The update installs `project_ai_driven`, which takes over
every record of `project_software` under its database id and renames the flag `is_software_project` to
`is_ai_driven`. A module that depends on `project_software` switches its dependency and the flag name in the same
deploy. `project_software` is left empty: uninstall it once no installed module depends on it, before the next
release removes it, and drop it from `addons.yaml`.

Everything the upgrade needs is in the *Installation* section of
[`project_ai_driven/README.rst`](project_ai_driven/README.rst).

## Development

- `ruff check` and `ruff format` (config in `ruff.toml`); `pre-commit install` runs both on commit.
- Tests run in CI on the OCA images for the branch's Odoo version (`.github/workflows/test.yml`).
- Module docs follow the OCA layout: fragments in `readme/`, `README.rst` generated from them.

## License

LGPL-3.0-or-later, see [LICENSE](LICENSE).
