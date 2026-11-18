from collections import Counter

from odoo import _, api, fields, models
from odoo.tools import config

# The number after the last hyphen: a project key may hold hyphens of its own.
RENAME_TASK_KEYS = """
    UPDATE project_task task
       SET key = project.key || '-' || substring(task.key from '[0-9]+$')
      FROM project_project project
     WHERE project.id = task.project_id
       AND project.id = %s
"""
NUMBER_KEYLESS_TASKS = """
    UPDATE project_task task
       SET key = numbered_task.key
      FROM (
            SELECT task.id,
                   project.key || '-' || (
                       first_number.number - 1 + row_number() OVER (PARTITION BY task.project_id ORDER BY task.id)
                   ) AS key
              FROM project_task task
              JOIN project_project project ON project.id = task.project_id
              JOIN unnest(%s::int[], %s::int[]) AS first_number(project_id, number)
                ON first_number.project_id = task.project_id
             WHERE task.key IS NULL
           ) numbered_task
     WHERE numbered_task.id = task.id
 RETURNING task.project_id
"""


class ProjectProject(models.Model):
    _inherit = "project.project"
    _rec_names_search = ["key", "name", "id"]

    # The names and columns of OCA project_key: a database that ran it keeps its keys (ADR 0014).
    key = fields.Char(size=10, index=True, copy=False)
    task_key_sequence_id = fields.Many2one("ir.sequence", string="Key Sequence", ondelete="restrict", copy=False)

    _sql_constraints = [("project_key_unique", "UNIQUE(key)", "Project key must be unique")]

    @api.onchange("name")
    def _onchange_name(self):
        for project in self.filtered(lambda record: not record.key):
            project.key = self._generate_key(project.name)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._gives_task_keys():
            return super().create(vals_list)
        self._add_generated_keys(vals_list)
        projects = super().create(vals_list)
        projects.filtered("key")._create_task_key_sequences()
        # Tasks created with the project came before its sequence.
        self.env["project.task"].with_context(active_test=False).search(
            [("project_id", "in", projects.ids), ("key", "=", False)], order="id"
        )._set_next_key()
        return projects

    def write(self, vals):
        if "key" not in vals or not self._gives_task_keys():
            return super().write(vals)
        rekeyed_projects = self.filtered(lambda record: record.key != vals["key"])
        result = super().write(vals)
        for project in rekeyed_projects:
            project._update_task_key_sequence()
            project._update_task_keys()
        return result

    def unlink(self):
        if not self._gives_task_keys():
            return super().unlink()
        # The sequence goes after its project: the project's foreign key restricts its deletion.
        sequences = self.sudo().task_key_sequence_id
        result = super().unlink()
        sequences.unlink()
        return result

    @api.model
    def _gives_task_keys(self):
        """While OCA project_key is loaded, it gives the keys as it did before: both would draw a
        number for every task and a sequence for every project (ADR 0014).
        """
        return "project_key" not in self.env.registry._init_modules

    @api.model
    def _add_generated_keys(self, vals_list):
        taken_keys = {vals["key"] for vals in vals_list if vals.get("key")}
        for vals in vals_list:
            if not vals.get("key"):
                vals["key"] = self._generate_key(vals.get("name"), taken_keys)
                taken_keys.add(vals["key"])

    @api.model
    def _generate_key(self, name, taken_keys=()):
        """Return a free key made of the first letter or digit of each word of name, or the first
        three of a single word, followed by the lowest counter that makes it free. Words are split
        at whitespace and keep their letters and digits only; a word without any gives no initial.
        Archived projects and taken_keys count. False without a name that has letters or digits,
        and while keys are off.

        The key with its counter is cut to the size of the column, which would cut it silently:
        the search for a free key cuts its value too, and would never find one.
        """
        if self._is_task_key_off() or not name:
            return False
        words = [alnum_word for word in name.split() if (alnum_word := "".join(filter(str.isalnum, word)))]
        if not words:
            return False
        base_key = (words[0][:3] if len(words) == 1 else "".join(word[0] for word in words)).upper()
        key_size = self._fields["key"].size
        projects = self.with_context(active_test=False)
        key, counter = base_key[:key_size], 0
        while key in taken_keys or projects.search_count([("key", "=", key)], limit=1):
            counter += 1
            key = "%s%s" % (base_key[: key_size - len(str(counter))], counter)
        return key

    def _is_task_key_off(self):
        # As in OCA project_key: the tests of other modules compare display names without keys.
        return config["test_enable"] and not self.env.context.get("test_project_key")

    def _get_next_task_key(self):
        """Return the next key of a task of the project, False without a project, its key or its
        sequence.
        """
        sequence = self.sudo().task_key_sequence_id
        if not self.key or not sequence or self._is_task_key_off():
            return False
        return sequence.next_by_id()

    def _get_task_key_sequence_values(self):
        return {"name": "%s %s" % (_("Project task sequence for project"), self.name), "prefix": "%s-" % self.key}

    def _create_task_key_sequences(self):
        sequences = (
            self.env["ir.sequence"]
            .sudo()
            .create(
                [
                    {
                        **project._get_task_key_sequence_values(),
                        "code": "project.task.key.%s" % project.id,
                        "company_id": project.company_id.id,
                    }
                    for project in self
                ]
            )
        )
        for project, sequence in zip(self, sequences):
            project.task_key_sequence_id = sequence

    def _update_task_key_sequence(self):
        """Give the sequence the project's key as prefix, or create it. A project whose key is
        cleared keeps its sequence as it is, and draws no key until it has one again.
        """
        self.ensure_one()
        if not self.key:
            return
        if self.task_key_sequence_id:
            self.task_key_sequence_id.sudo().write(self._get_task_key_sequence_values())
        else:
            self._create_task_key_sequences()

    def _update_task_keys(self):
        """Give the tasks of the project its new key, each keeping its number; a cleared key
        clears theirs.
        """
        self.ensure_one()
        self.flush_recordset(["key"])
        self.env["project.task"].flush_model(["key", "project_id"])
        self.env.cr.execute(RENAME_TASK_KEYS, (self.id,))
        self.env["project.task"].invalidate_model(["key", "display_name"])

    @api.model
    def _set_missing_keys(self):
        """Give each project without a key one from its name and a sequence, and number its tasks
        without a key in the order they were created. Projects that have a key keep it and their
        tasks theirs.
        """
        projects = self.with_context(active_test=False).search([("key", "=", False)], order="id")
        for project in projects:
            key = self._generate_key(project.name)
            if key:
                project.key = key
        keyed_projects = projects.filtered("key")
        # While OCA project_key is loaded, its write gives the key and no sequence.
        keyed_projects.filtered(lambda project: not project.task_key_sequence_id)._create_task_key_sequences()
        keyed_projects._number_keyless_tasks()

    def _number_keyless_tasks(self):
        """Give the tasks without a key of each project the next numbers of its sequence, archived
        ones included, in the order they were created, and move the sequence past them.

        One SQL statement writes the keys: a key given afterwards is no edit of a task, and its
        write date stays.
        """
        if not self:
            return
        sequences = [project.sudo().task_key_sequence_id for project in self]
        first_numbers = [sequence.number_next_actual for sequence in sequences]
        self.flush_recordset(["key"])
        self.env["project.task"].flush_model(["key", "project_id"])
        self.env.cr.execute(NUMBER_KEYLESS_TASKS, (self.ids, first_numbers))
        task_counts = Counter(project_id for (project_id,) in self.env.cr.fetchall())
        self.env["project.task"].invalidate_model(["key", "display_name"])
        for project, sequence, first_number in zip(self, sequences, first_numbers):
            if task_counts[project.id]:
                sequence.write({"number_next": first_number + task_counts[project.id]})
