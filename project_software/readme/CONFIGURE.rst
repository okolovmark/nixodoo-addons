* On a project, tick **Software Project** (*Settings* tab, above
  *Visibility*) to show the Technical Solution tab on its tasks.
* Checklist items and test cases are managed by **Project / User**. The
  Checklist, Test Cases and Technical Solution tabs are hidden from users
  without that group.
* To be notified of checklist items that are neither assigned to you nor
  created by you, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items with an account that
  has no email address passes ``mail_auto_subscribe_no_notify`` in the
  context; without it Odoo refuses to send the notification and the write
  fails.
