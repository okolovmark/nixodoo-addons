* On a project, switch on **AI-driven Project** (*Settings* tab, *Tasks
  Management*) to show the Technical Solution and Pull Requests tabs and the
  Resolution on its tasks.
* Below it, on an AI-driven project, **Stages Requiring a Resolution** lists
  the stages a task enters only with a Resolution, and with a pull request
  when the Resolution is Code. Only stages of the project can be picked.
  Leave it empty to switch the closing rule off.
* Checklist items and test cases are managed by users with the **User**
  access of *Project* (Settings > Users, the *Access Rights* tab of a user).
  The Checklist, Test Cases and Technical Solution tabs are hidden from users
  without it.
* Pull requests are read by every internal user and created and edited by
  users with the **User** access of *Project*. No access right allows
  deleting one.
* The stage log is read by system administrators (``base.group_system``)
  only. No access right allows creating, changing or deleting a row.
* To be notified of checklist items that are neither assigned to you nor
  created by you, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items passes
  ``mail_auto_subscribe_no_notify`` in the context to keep the write silent:
  no assignment notification, no checklist post. Without the key the
  notifications are queued as for any user, also for an account that has no
  email address to send them from.
