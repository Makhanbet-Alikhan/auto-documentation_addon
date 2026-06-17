# -*- coding: utf-8 -*-
"""Stored documentation for a single model used by a module."""
from odoo import fields, models


class DocModelInfo(models.Model):
    _name = "doc.model.info"
    _description = "Auto Doc - Documented Model"
    _order = "technical_name"

    doc_module_id = fields.Many2one(
        "doc.module", string="Documentation", required=True, ondelete="cascade"
    )
    technical_name = fields.Char(
        string="Technical Name", required=True,
        help="The ORM model name, e.g. res.partner.",
    )
    display_name = fields.Char(string="Display Name")
    description = fields.Text(
        string="Description",
        help="Composed from the model class docstring and field comments.",
    )
    # The full field table stored as JSON for the renderer to iterate over.
    field_table_json = fields.Json(string="Field Table")
    field_count = fields.Integer(string="Field Count", default=0)
