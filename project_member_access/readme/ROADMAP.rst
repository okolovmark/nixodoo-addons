* An Admin of a Public or Private project can move a task in with new
  assignees and move it out again; a module restricting assignee changes in
  Standard projects does not see that change.
* Inside a Public or Private project only an Admin converts a checklist
  item that has an assignee into a sub-task: the sub-task is created with
  the item's assignee.
* The checklist items of the next occurrence of a recurring task in a
  Private project keep their assignee and reviewer even when these are no
  longer members of it. An item whose assignee and reviewer have both left
  can be changed by neither, and comes back on every occurrence.
* The quick-create card, the kanban cards and the lists of tasks keep the
  Assignees editable and offer non-members of a Private project: the save
  refuses what the task form does not offer.
* Uninstalling the module drops the Access modes and the members: every
  project is left to its Visibility again.
