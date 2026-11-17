def check_task_line_move(lines, vals):
    """Core checks write access on the task a line leaves, not on the one a
    write of task_id moves it to: check that one too.
    """
    if "task_id" in vals:
        lines.env["project.task"].browse(vals["task_id"]).check_access("write")
