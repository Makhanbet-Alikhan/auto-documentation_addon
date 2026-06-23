# -*- coding: utf-8 -*-
"""Deterministic Russian-language manual text provider (user-friendly style).

Generates plain-language, step-by-step wording for every section of the
user manual so that a non-technical reader can understand how to use the
documented module without any developer knowledge.

All texts are written in Russian.

Key design principle — UNIQUE TEXT PER MENU:
  Every menu gets its own description, field list and steps derived from the
  real ORM field metadata stored in ``doc.menu.fields_meta_json``.  The
  generic fallback is used only when no model metadata is available.

Group-2 fix:
  * function_for_create() now accepts an optional ``fields_meta_override``
    parameter so callers can pass a live ORM field dict when the stored
    fields_meta_json is empty — steps are NEVER the single generic sentence.
  * function_for_inherited_create() generates a dedicated "Create <record>"
    function for base models extended via _inherit (e.g. event.event).
    It uses live ORM introspection to produce real per-field steps and
    clearly states which module owns the creation form.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Fields that are always present in every Odoo model but carry no
# user-visible meaning in a user manual — skip them.
_SYSTEM_FIELDS = frozenset({
    "id", "create_uid", "create_date", "write_uid", "write_date",
    "__last_update", "display_name", "message_ids", "message_follower_ids",
    "message_partner_ids", "message_is_follower", "message_unread_counter",
    "message_needaction_counter", "message_has_error", "message_attachment_count",
    "activity_ids", "activity_state", "activity_user_id", "activity_type_id",
    "activity_date_deadline", "activity_summary", "activity_exception_decoration",
    "activity_exception_icon", "website_message_ids", "has_message",
})

# Human-readable type hints for common Odoo field types
_TYPE_HINTS = {
    "char":        "текстовое поле",
    "text":        "многострочный текст",
    "html":        "текст с форматированием",
    "integer":     "целое число",
    "float":       "числовое значение",
    "monetary":    "денежная сумма",
    "boolean":     "флаг (да / нет)",
    "date":        "дата",
    "datetime":    "дата и время",
    "selection":   "выбор из списка",
    "many2one":    "связанная запись",
    "many2many":   "несколько связанных записей",
    "one2many":    "вложенный список",
    "binary":      "файл / изображение",
    "image":       "изображение",
    "reference":   "ссылка на запись",
    "json":        "JSON-данные",
}


class DocTextDefaults(models.AbstractModel):
    _name = "doc.text.defaults"
    _description = "DPF Docs - Manual Text Defaults"

    # ------------------------------------------------------------------
    # Cover / introduction defaults
    # ------------------------------------------------------------------
    @api.model
    def manual_defaults(self, doc_module):
        """Return a dict of default manual-metadata values for a doc.module."""
        system_name = doc_module.system_name or (
            'Система "%s"' % (doc_module.name or doc_module.technical_name)
        )
        platform = doc_module.platform_version or "Odoo 19"
        return {
            "system_name": system_name,
            "manual_version": doc_module.manual_version or "1.0",
            "developer": doc_module.developer or "ТОО «Разработчик»",
            "city_year": doc_module.city_year or "Астана 2025",
            "platform_version": platform,
            "intro_user_categories": self._default_user_categories(),
            "intro_scope": self._default_scope(system_name, platform),
            "intro_purpose": self._default_purpose(system_name),
            "intro_conventions": self._default_conventions(),
            "content_purpose": self._default_content_purpose(system_name),
            "content_materials": self._default_materials(),
            "content_preparation": self._default_preparation(),
            "bibliography": self._default_bibliography(platform),
            "glossary": self._default_glossary(),
        }

    @staticmethod
    def _default_user_categories():
        return (
            "Данная система предназначена для следующих категорий пользователей:\n"
            "Менеджеры — сотрудники среднего звена, ответственные за ведение "
            "процессов, создание и контроль записей в системе. Требуется базовый "
            "опыт работы с компьютером и браузером.\n"
            "Аналитики — специалисты, работающие с отчётами и данными системы. "
            "Требуется умение читать таблицы и фильтровать данные.\n"
            "Операционные пользователи (исполн