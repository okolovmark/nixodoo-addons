import json
import math

from odoo import models
from odoo.tools import format_datetime

# Excel's limit on a sheet's name, which it compares without case.
SHEET_NAME_MAX_CHARS = 31


class AiReportXlsx(models.AbstractModel):
    _name = "report.ai_assistant.ai_report_xlsx"
    _description = "AI report export"
    _inherit = "report.report_xlsx.abstract"

    def generate_xlsx_report(self, workbook, data, reports):
        """A sheet of the report's facts, then a sheet per dataset and merge: its columns, then its rows.

        `reports` are read as the user who exports, so the record rules decide
        who may; the data is the owner's snapshot, as the app shows it.
        """
        bold = workbook.add_format({"bold": True})
        sheet_names = set()
        for report in reports:
            summary_sheet = workbook.add_worksheet(self._sheet_name(self.env._("Report"), sheet_names))
            facts = (
                (self.env._("Title"), report.name),
                (self.env._("Owner"), report.user_id.name),
                (self.env._("Data date"), format_datetime(self.env, report.data_date)),
                (self.env._("Conclusions written on data of"), format_datetime(self.env, report.conclusions_date)),
            )
            for row_index, (label, value) in enumerate(facts):
                summary_sheet.write_string(row_index, 0, label, bold)
                summary_sheet.write_string(row_index, 1, value or "")
            for name, table in json.loads(report.data or "{}").items():
                sheet = workbook.add_worksheet(self._sheet_name(name, sheet_names))
                for column_index, column in enumerate(table["columns"]):
                    sheet.write_string(0, column_index, column, bold)
                for row_index, row in enumerate(table["rows"], start=1):
                    for column_index, column in enumerate(table["columns"]):
                        self._write_cell(sheet, row_index, column_index, row.get(column))
                if table.get("has_more"):
                    sheet.write_string(
                        len(table["rows"]) + 1,
                        0,
                        self.env._("The first %s rows: more match.", len(table["rows"])),
                        bold,
                    )

    def _sheet_name(self, name, sheet_names):
        """`name`, or `name (2)`, `name (3)`... when a sheet of the workbook has it already, within
        SHEET_NAME_MAX_CHARS; added to `sheet_names`, the names taken, in lowercase."""
        sheet_name, copy = name[:SHEET_NAME_MAX_CHARS], 1
        while sheet_name.lower() in sheet_names:
            copy += 1
            suffix = " (%s)" % copy
            sheet_name = name[: SHEET_NAME_MAX_CHARS - len(suffix)] + suffix
        sheet_names.add(sheet_name.lower())
        return sheet_name

    def _write_cell(self, sheet, row_index, column_index, value):
        """One value in its own cell type; text is never read as a formula. False, null and a NaN or infinite
        number stay blank."""
        if value is None or value is False or (isinstance(value, float) and not math.isfinite(value)):
            return
        if value is True:
            sheet.write_boolean(row_index, column_index, value)
        elif isinstance(value, (int, float)):
            sheet.write_number(row_index, column_index, value)
        elif isinstance(value, list) and len(value) == 2 and isinstance(value[1], str):
            sheet.write_string(row_index, column_index, value[1])
        elif isinstance(value, list):
            sheet.write_string(row_index, column_index, ", ".join(map(str, value)))
        else:
            sheet.write_string(row_index, column_index, str(value))
