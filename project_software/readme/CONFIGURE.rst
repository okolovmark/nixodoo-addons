* On a project, switch on **Software Project** (*Settings* tab, *Tasks
  Management*) to show the Technical Solution tab on its tasks.
* Checklist items and test cases are managed by users with the **User**
  access of *Project* (Settings > Users, the *Access Rights* tab of a user).
  The Checklist, Test Cases and Technical Solution tabs are hidden from users
  without it.
* To be notified of checklist items that are neither assigned to you nor
  created by you, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items passes
  ``mail_auto_subscribe_no_notify`` in the context to keep the write silent:
  no assignment notification, no checklist post. Without the key the
  notifications are queued as for any user, also for an account that has no
  email address to send them from.
