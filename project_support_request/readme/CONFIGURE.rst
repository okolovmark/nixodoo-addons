A Settings administrator ticks **Develops This Odoo** on the form of the
AI-driven project that develops this database (``project_ai_driven``). Until
an active project carries it, the Support item is hidden.

A request is created with a task type and no estimate in the first unfolded
stage of that project: keep that stage out of **Stages Requiring a Type and
an Estimate** (``project_ai_driven``), or the request is refused and the
item answers *Error creating the support request*.

The install splits Odoo's company rule of tasks, *Project/Task:
multi-company*: it keeps write, create and delete, and the module's rule
*Project/Task: multi-company, read; own support requests in any company*
reads. Keep the module's rule active: without it no company restricts
reading tasks. Each update of the module sets Odoo's rule so again, unless
Read is the only permission left on it: a rule keeps one, so it keeps Read,
the log warns, and reads go by Odoo's rule without the exception.

The install restricts reading tasks by company on a database that had
lifted that restriction: one that had unticked Read on Odoo's rule, or
archived or deleted that rule, reads by the module's rule once installed.
Such a database archives the module's rule after installing.

The module's rule reads by the domain Odoo's rule (``project.task_comp_rule``)
has at that moment and adds the exception to it. A change to the domain of
Odoo's rule applies to reading and writing at once, and stays through
updates of the module.

Groups given to Odoo's rule make it restrict their members only, and the
module's rule follows: users outside the groups are not restricted by it,
and members read only the tasks of the companies they selected, plus their
own requests. Odoo combines a member's group rules with OR for writing, so
another group rule of tasks can let them write a task of another company;
the module's rule is global and combines with every other rule with AND,
so members may read fewer tasks than before the install.

Archiving or deleting Odoo's rule lifts the company restriction from
writing tasks only: reads keep the domain Odoo ships the rule with,
``[('company_id', 'in', company_ids)]``, and the exception. They keep it
for every user: the groups of an archived Odoo rule no longer count, and
users outside them, unrestricted while it was active, read by that domain
too. A copy of Odoo's rule made after the install has no read either, so
an edited copy restricts writing only. To lift the company restriction
from reading tasks as well, archive the module's rule too.

The uninstall archives the copies of the module's rule and gives Read to
Odoo's rule and to every active global rule of tasks without Read whose
name starts with the name of Odoo's rule or whose domain is that rule's,
as it stands or as Odoo ships it, whatever took Read off it, the module
or an administrator: the uninstall restores these rules as Odoo ships its
own, with Read. A database that had unticked Read on Odoo's rule, or kept
a copy of it without Read, before installing gets Read back on them too:
untick it again after the uninstall.

The design record is
``docs/adr/0017-a-filer-reads-their-own-support-requests-in-any-company.md``
in this repository.
