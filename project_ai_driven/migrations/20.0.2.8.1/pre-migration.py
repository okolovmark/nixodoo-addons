import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Blank the stored arch of this module's extension views before the data files load.

    Validating a view of the update combines every stored extension of its parent, and the ones not
    rewritten yet still name fields and buttons of the earlier version. The update rewrites each view
    a data file names and deletes the others at its end.
    """
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_ui_view
           SET arch_db = jsonb_build_object('en_US', '<data/>')
          FROM ir_model_data imd
         WHERE imd.model = 'ir.ui.view'
           AND imd.res_id = ir_ui_view.id
           AND imd.module = %s
           AND ir_ui_view.mode = 'extension'
        """,
        ["project_ai_driven"],
    )
    _logger.info("Extension views blanked until the data files rewrite them: %d", cr.rowcount)
