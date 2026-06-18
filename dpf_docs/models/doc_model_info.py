# -*- coding: utf-8 -*-
"""doc.model.info — stores introspected model metadata for documentation.

Fields added in v7:
  - business_logic_text : Plain-language description of workflow/buttons
  - is_primary          : True for the module's primary (main) model
"""
from odoo import fields, models


class DocModelInfo(models.Model):
    _name = "doc.model.info"
    _description = "Auto Doc - Model Information"
    _order = "is_primary desc, display_name asc"

    doc_module_id = fields.Many2one(
        "doc.module",
        string="Модуль",
        ondelete="cascade",
        required=True,
        index=True,
    )
    technical_name = fields.Char(string="Техническое имя", required=True)
    display_name = fields.Char(string="Название")
    description = fields.Text(string="Описание")
    field_table_json = fields.Json(
        string="Поля (только пользовательские)",
        help=(
            "JSON-список полей, которые пользователь заполняет на форме. "
            "Системные, вычисляемые и readonly-поля исключены."
        ),
    )
    field_count = fields.Integer(string="Кол-во полей")

    business_logic_text = fields.Text(
        string="Бизнес-логика",
        help=(
            "Описание автоматического поведения системы: "
            "статусы записи, доступные кнопки действий."
        ),
    )

    is_primary = fields.Boolean(
        string="Основная модель",
        default=False,
        help=(
            "True для главной модели модуля. Определяется автоматически."
        ),
    )
