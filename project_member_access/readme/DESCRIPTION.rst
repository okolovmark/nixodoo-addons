A project gets an **Access** mode and members with a role: Reader,
Contributor, Editor or Admin.

* **Standard**: the project's Visibility decides, as without this module;
  its members change nothing.
* **Public**: every internal user reads the project and its tasks.
* **Private**: only its members read them. Project Administrators read every
  project and act as Readers without a role.

Inside a Public or Private project the role decides who creates, edits,
assigns, moves and deletes tasks, and who changes the members, stages,
milestones and updates of the project. Settings administrators are Admin of
every project. Portal users and followers get nothing from such a project.
Pull requests of its tasks are read by whoever reads one of these tasks, and
changed only by whoever edits all of them.

Notifications of such a project, of its tasks and of its updates, by email or
in the Discuss inbox, reach only the followers and recipients who can read
them; the followers list itself does not change.

Portal links do not open such a project or its tasks: their access tokens are
cleared when the project becomes Public or Private, or a task moves into it;
while it stays so, the project and its tasks keep no token and its share
buttons are hidden. Attachments keep theirs: such a token opens that one file
only, as the images pasted into a description or a message need.
