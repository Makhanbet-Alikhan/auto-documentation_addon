# -*- coding: utf-8 -*-
"""Stored "function" entry for the user-manual style export.

Each ``doc.function`` record describes a single user-facing function (a screen
or workflow step) the way the reference user manual does it:

* a numbered title         -> "Функция N: <name>."
* a description            -> "Описание:"
* prerequisites            -> "Требования:" (rendered in red in the export)
* an ordered list of steps -> "Порядок выполнения:" (numbered)
* an illustrating image    -> centered screenshot + "Рис.N <caption>"
* the expected outcome     -> "Результат:"

Functions are normally generated automatically from the module's menu tree
(one function per documented screen), but they are editable so a human can
refine the wording before exporting.
"""
from odoo import api, fields, models


class DocFunction(models.Model):
    _name = "doc.function"
    _description = "Auto Doc - Manual Function Entry"
    _order = "sequence, id"

    doc_module_id = fields.Many2one(
        "doc.module",
        string="Documentation",
        required=True,
        ondelete="cascade",
    )
    # Source menu this function was generated from (optional, kept for trace).
    doc_menu_id = fields.Many2one(
        "doc.menu",
        string="Source Menu",
        ondelete="set null",
        help="Menu/screen this function entry was generated from.",
    )

    sequence = fields.Integer(string="Sequence", default=10)
    # Auto-assigned 1-based index used as "Функция N" in the export.
    number = fields.Integer(
        string="Function Number",
        help="1-based position used as 'Функция N' in the manual.",
    )

    name = fields.Char(string="Function Title", required=True)
    description = fields.Text(
        string="Description",
        help="Rendered after the bold 'Описание:' label.",
    )
    requirements = fields.Text(
        string="Requirements",
        help="Rendered after the bold 'Требования:' label, in red text.",
    )
    steps = fields.Text(
        string="Steps",
        help="One step per line. Rendered as a numbered 'Порядок выполнения:' list.",
    )
    result = fields.Text(
        string="Result",
        help="Rendered after the bold 'Результат:' label.",
    )

    # Illustration shown in the manual. Two ways to fill it:
    #   * "menu"   -> copied automatically from the source menu screenshot
    #                 (captured by the Playwright worker);
    #   * "manual" -> uploaded by hand in the function form. Manual uploads are
    #                 never overwritten by the menu re-sync before export.
    screenshot = fields.Binary(string="Screenshot", attachment=True)
    screenshot_filename = fields.Char(string="Screenshot Filename")
    screenshot_source = fields.Selection(
        [
            ("none", "None"),
            ("menu", "From Menu (auto)"),
            ("manual", "Uploaded Manually"),
        ],
        string="Screenshot Source",
        default="none",
        help="How the current screenshot was provided. Manual uploads are "
             "preserved and never replaced by the automatic menu sync.",
    )
    screenshot_caption = fields.Char(
        string="Figure Caption",
        help="Text shown under the figure, after the 'Рис.N' label.",
    )

    @api.onchange("screenshot")
    def _onchange_screenshot(self):
        """Flag hand-uploaded images so the menu sync never overwrites them."""
        for func in self:
            if func.screenshot and func.screenshot_source != "menu":
                func.screenshot_source = "manual"
            elif not func.screenshot:
                func.screenshot_source = "none"

    def step_lines(self):
        """Return the cleaned, non-empty step lines for ordered rendering."""
        self.ensure_one()
        if not self.steps:
            return []
        return [line.strip() for line in self.steps.splitlines() if line.strip()]
