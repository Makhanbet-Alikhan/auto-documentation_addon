# -*- coding: utf-8 -*-
"""doc.menu — represents a single menu/screen in the documented module.

v7 additions:
  - groups_info: comma-separated list of access groups (roles) for this screen
"""
from odoo import fields, models


class DocMenu(models.Model):
    _name = "doc.menu"
    _description = "Auto Doc - Menu / Screen"
    _order = "sequence asc, id asc"

    doc_module_id = fields.Many2one(
        "doc.module",
        string="Модуль",
        ondelete="cascade",
        required=True,
        index=True,
    )
    name = fields.Char(string="Название меню", required=True)
    complete_name = fields.Char(string="Полный путь")
    menu_xmlid = fields.Char(string="XML ID")
    sequence = fields.Integer(string="Порядок", default=10)
    odoo_menu_id = fields.Integer(string="ID меню Odoo")
    action_id = fields.Integer(string="ID действия")
    res_model = fields.Char(string="Модель")
    view_modes = fields.Char(string="Режимы просмотра")
    web_url = fields.Char(string="URL")
    caption = fields.Text(
        string="Описание экрана",
        help="Автоматически сгенерированное описание для пользователей.",
    )
    caption_source = fields.Selection(
        [
            ("generated", "Сгенерировано"),
            ("task",      "Из задачи проекта"),
            ("manual",    "Вручную"),
        ],
        string="Источник описания",
        default="generated",
    )
    caption_task_name_snapshot = fields.Char(
        string="Задача-источник",
        help="Название задачи из снапшота, из которой взято описание.",
    )
    capture_state = fields.Selection(
        [
            ("pending",  "Ожидает"),
            ("done",     "Готово"),
            ("skipped",  "Пропущено"),
            ("error",    "Ошибка"),
        ],
        string="Статус скриншота",
        default="skipped",
    )
    screenshot = fields.Binary(string="Скриншот")
    fields_meta_json = fields.Json(
        string="Метаданные полей (пользовательские)",
        help="Только поля, которые пользователь заполняет на форме.",
    )

    groups_info = fields.Char(
        string="Роли доступа",
        help=(
            "Группы пользователей Odoo, которым доступен этот экран. "
            "Заполняется автоматически из настроек меню и действий."
        ),
    )
