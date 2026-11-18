**Roles in a Public or Private project**

============================================== ====== =========== ====== =====
Action                                         Reader Contributor Editor Admin
============================================== ====== =========== ====== =====
Read tasks, post in the chatter                yes    yes         yes    yes
Create a task, or move one in                  no     yes         yes    yes
Edit an own task (creator, assignee, UAT user) yes    yes         yes    yes
Edit somebody else's task                      no     no          yes    yes
Move the stage of a task one is assigned to    yes    yes         yes    yes
Move the stage of another task                 no     no          yes    yes
Change assignees, on create too                no     no          no     yes
Change UAT users of a task one is assigned to  yes    yes         yes    yes
Change UAT users of other tasks, on create too no     no          yes    yes
Delete a task                                  no     no          no     yes
Members, Access, stages, milestones, updates   no     no          no     yes
============================================== ====== =========== ====== =====

Without a role, an internal user is a Reader of a Public project, a Project
Administrator a Reader of every project, and a Settings administrator an
Admin of every project.

**Work in such a project**

* Create tasks without assignees unless you are an Admin: clear the
  Assignees a screen fills in (the *Sub-tasks* tab of a task copies the
  parent's, *My Tasks* adds you); an Admin assigns them. Leave the UAT
  users empty too unless you are an Editor or an Admin: an assignee of the
  task sets them afterwards.
* Move a task along the stages when you are one of its assignees; Editors
  and Admins move any task.
* Move a task out of the project only with the sub-tasks you may edit: a
  sub-task without a project of its own leaves with its parent.
* Admins keep the *Members* tab of the project form: add a user with a
  role, change a role, remove a member. A member's user is not changed:
  remove the member and add the other user. The last Admin stays until
  another member is Admin.
* Following a task or the project opens nothing: a follower who cannot read
  it gets no notification of it, and no portal link opens it.
