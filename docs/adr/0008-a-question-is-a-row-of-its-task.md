# A question is a row of its task

Status: accepted (2026-11-17)

The clarifying questions of a task were one Html field, Questions, and their answers another, Answers, on the Technical Solution tab. An answer was tied to its question only by its place in the other block. Nothing said which questions were still open, whom a question was for, which round asked it, or who answered and when, and a tool working the task over the API read and wrote two blocks of text.

From 16.0.2.7.0 a question is a row of `project.task.question`, held by the task field `question_ids`: a round (a name or a date), the question, the internal user it is addressed to, the answer, and a status, Open, Answered or Dropped. The status follows the answer, stamped the way a test case's result is (ADR 0005): an Open row that gets an answer showing text, an image, a video or an icon becomes Answered unless the same call sets the status, and a create or a write that makes a row Answered records Answered by, the user who makes it (also under `sudo()`), and Answered on, now, unless the call gives them. A row already Answered keeps its stamp; Open clears it and keeps the answer; Dropped leaves it. A row follows the access of its task through `project.task.access.mixin`, which holds the two overrides ADR 0007 gave the checklist item, so both lines of a task share one rule. The Technical Solution tab lists the rows above the solution, as cards on a phone. Copying a task copies its rows with all their values.

The module no longer declares `questions` and `answers`. Their text is the history of the databases that hold it: a module of such a database that keeps it declares the two fields itself and is updated in the same run.

Rejected: pairing questions and answers by position whatever their shape: paragraph by paragraph, or list items up to the shorter list. An answer skipped or added in the middle moves every answer after it under the wrong question, and text outside the items would have no row.

Rejected: filling Answered by and Answered on of converted rows from the names and dates written in the answers. The text names people and dates in no fixed form; a stamp read from it would state a user and a time nobody recorded.

**Existing questions and answers.** The post-migration, and the post-init hook after a take-over from the former module names, convert every task, archived ones included. A block is paired only where its shape says what goes with what; any other block is kept whole, since a split by guess could put an answer under the wrong question or lose the text between the parts, and a row kept whole loses nothing.

- Both blocks are lists alone, as many lists on each side and as many items in each pair of lists: a row per pair of items, Answered, or Open where the answer item is blank; a pair of two blank items gives no row, and a block where the items of a pair of lists with a heading are all blank, every pair blank included, is kept whole: the heading would be in no row. Lists alone are top-level lists, each after at most one block of text, its heading, which gives the round as plain text. A list nested in a list belongs to the item before it. The heading of an answer list other than the round of its questions is put in front of each of its answers: it says who answered and when.
- Any other shape on both sides: one row of both blocks as they are, Answered. A block is not lists alone with text around a list or inside it outside an item, a second heading, an image, video, embedded frame, icon or list in a heading, or a list inside another block or nested before any item; two blocks of lists alone do not pair when the number of lists, or of items in a pair of lists, differs.
- Questions alone: a row per item that is not blank, Open, or one row of the whole block when it is not lists alone or the items of a list with a heading are all blank, every item blank included.
- Answers alone: one row of the whole block without a question, Answered.
- Html that shows no text, image, video, embedded frame or icon is blank; two blank blocks give no row.

SQL inserts the rows as the superuser, in the order of the Html, without Answered by or Answered on, and writes nothing on the tasks. A task that has rows is skipped, so a second run inserts nothing. Before the data files load, the pre-migration takes the two fields out of the stored views of this module, in every translation; without that, an update without dev flags fails validating the task form.

**Consequences.**

- Clients read and write rows: `question_ids` on the task, `answer` and `state` on a row. Without another module declaring them, `questions` and `answers` are unknown fields.
- Without another module declaring the two fields, the update deletes them with their columns at its end, and the content is in the rows only. A module that keeps them is updated in the same run; an update of this module while that module's older version, which does not declare them, is on disk drops the columns.
- A row kept whole holds every question and answer of its task, to be split by hand.
- Pairing trusts the order of two lists of as many items: it pairs them also where an answer was written for another question.
- The round of a converted row is the text of its heading, without its formatting.
- Rows have no chatter: a change of status or answer leaves no trace but the stamp.

Reversal cost: declare the two Html fields again and write each task's rows back into them as text. The round, the addressee, the status and the stamps have no place there; a row kept whole goes back as it was.
