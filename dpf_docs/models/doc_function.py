# -*- coding: utf-8 -*-
"""
Stored function entry for the user-manual style export.

Each doc.function record describes a single user-facing function (a screen
or workflow step) the way the reference user manual does it:

* a numbered title         -> "Function N: <name>."
* a description            -> "Description:"
* prerequisites            -> "Requirements:" (rendered in red in the export)
* an ordered list of steps -> "Steps:" (numbered)
* an illustrating image    -> centered screenshot + "Fig.N <caption>"
* the expected outcome     -> "Result:"

Functions are normally generated automatically from the module's menu tree
(one function per documented screen), but they are also created from project
task snapshots during enrichment — one function per tagged subtask.
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
    doc_menu_id = fields.Many2one(
        "doc.menu",
        string="Source Menu",
        ondelete="set null",
        help="Menu/screen this function entry was generated from.",
    )

    # Link back to the project task snapshot that created this function.
    # Stored as an Integer (not a FK) so it survives snapshot deletion.
    source_task_id = fields.Integer(
        string="Source Task ID",
        default=0,
        help=(
            "ID of the project.task at import time. Used to match this function "
            "to its snapshot on re-enrichment (upsert instead of duplicate)."
        ),
    )

    sequence = fields.Integer(string="Sequence", default=10)
    number = fields.Integer(
        string="Function Number",
        help="1-based position used as 'Function N' in the manual.",
    )

    name = fields.Char(string="Function Title", required=True)
    description = fields.Text(
        string="Description",
        help="Rendered after the bold 'Description:' label.",
    )
    requirements = fields.Text(
        string="Requirements",
        help="Rendered after the bold 'Requirements:' label, in red text.",
    )
    steps = fields.Text(
        string="Steps",
        help="One step per line. Rendered as a numbered 'Steps:' list.",
    )
    result = fields.Text(
        string="Result",
        help="Rendered after the bold 'Result:' label.",
    )

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
        help="Text shown under the figure, after the 'Fig.N' label.",
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
