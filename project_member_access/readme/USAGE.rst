**Roles in a Public or Private project**

================================================================== ====== =========== ====== =====
Action                                                             Reader Contributor Editor Admin
================================================================== ====== =========== ====== =====
Read tasks, post in the chatter                                    yes    yes         yes    yes
Create a task, or move one in                                      no     yes         yes    yes
Edit an own task (creator, assignee, UAT user)                     yes    yes         yes    yes
Edit somebody else's task                                          no     no          yes    yes
Move the stage of a task one is assigned to, close it or reopen it yes    yes         yes    yes
Move the stage of another task, close or reopen it                 no     no          yes    yes
Change assignees, on create too                                    no     no          no     yes
Delete a task                                                      no     no          no     yes
Members, Access, stages, milestones, updates                       no     no          no     yes
================================================================== ====== =========== ====== =====

Without a role, an internal user is a Reader of a Public project, a Project
Administrator a Reader of every project, and a Settings administrator an
Admin of every project.

**Work in such a project**

* Create tasks without assignees unless you are an Admin: clear the
  Assignees a screen fills in (the *Sub-tasks* tab of a task copies the
  parent's, *My Tasks* and *All Tasks* add you); an Admin assigns them.
* Move a task along the stages, and mark it Done or Cancelled or reopen it,
  when you are one of its assignees; Editors and Admins do so on any task.
  The creator and the UAT users of a task set its other states, Changes
  Requested and Approved included.
* Admins keep the *Members* tab of the project form: add a user with a
  role, change a role, remove a member. A member's user is not changed:
  remove the member and add the other user. The last Admin stays until
  another member is Admin.
* Following a task or the project, or being one of its Team Members, opens
  nothing: a user who cannot read it gets no notification of it, and no
  portal link opens it.
