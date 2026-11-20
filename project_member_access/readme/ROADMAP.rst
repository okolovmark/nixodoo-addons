* An Admin of a Public or Private project can move a task in with new
  assignees and move it out again; a module restricting assignee changes in
  Standard projects does not see that change.
* A task moved out of a Public or Private project is judged by the project
  it lands in, its assignees and UAT users too: one moved out with them
  cleared and moved back in has lost them without the role that changes
  them inside.
* Inside a Public or Private project only an Admin converts a checklist
  item that has an assignee into a sub-task: the sub-task is created with
  the item's assignee.
* The checklist items of the next task of a recurrence in a Private project
  keep their assignee and reviewer, also those who are no longer members of
  it. An item whose assignee and reviewer have both left can be changed by
  neither, and it comes back on every next task.
* Uninstalling the module drops the Access modes and the members: every
  project is left to its Visibility again.
