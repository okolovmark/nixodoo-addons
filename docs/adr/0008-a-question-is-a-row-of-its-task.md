# A question is a row of its task

Status: accepted (2026-11-17)

The 16.0 branch decided this first. Its record, `docs/adr/0008-a-question-is-a-row-of-its-task.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The row.** A clarifying question is a row of `project.task.question`, held by the task field `question_ids`: a round (a name or a date), the question, the internal user it is addressed to, the answer, and a status, Open, Answered or Dropped. The status follows the answer, stamped the way a test case's result is (ADR 0005): an Open row that gets an answer showing text, an image, a video or an icon becomes Answered unless the same call sets the status, and a create or a write that makes a row Answered records Answered by, the user who makes it (also under `sudo()`), and Answered on, now, unless the call gives them. A row already Answered keeps its stamp; Open clears it and keeps the answer; Dropped leaves it. A row follows the access of its task as a checklist item does (ADR 0007). The Technical Solution tab lists the rows above the solution, as cards on a phone. Copying a task copies its rows with all their values.

The module does not declare the Html fields `questions` and `answers`.

**What differs from 16.0.**

- Nothing is converted (ADR 0001). 16.0 turns the Html of each task into rows by SQL. Here an update from an earlier 20.0 version deletes the two fields with their columns and their text, and the tasks start without rows.
- The access of a row is the domain of its access right, not `project.task.access.mixin` (ADR 0007).

**Consequences.**

- Clients read and write rows: `question_ids` on the task, `answer` and `state` on a row. `questions` and `answers` are unknown fields.
- On a database updated from an earlier 20.0 version, the text of the two fields is lost unless it was exported before the update.
- Rows have no chatter: a change of status or answer leaves no trace but the stamp.

Reversal cost: declare the two Html fields again and write each task's rows back into them as text. The round, the addressee, the status and the stamps have no place there.
