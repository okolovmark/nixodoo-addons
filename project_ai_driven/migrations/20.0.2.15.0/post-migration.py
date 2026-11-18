import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Convert the test case states stored under the keys of 20.0.2.4.1 and earlier, which this version's
    selection no longer has: a copy of such a case fails on its state.
    """
    cr.execute(
        """
        UPDATE project_task_testcase
           SET state = CASE state
                       WHEN 'todo' THEN 'not_run'
                       WHEN 'done' THEN 'passed'
                       WHEN 'cancelled' THEN 'not_applicable'
                       END
         WHERE state IN ('todo', 'done', 'cancelled')
        """
    )
    _logger.info("Test cases converted from a former state: %d", cr.rowcount)
