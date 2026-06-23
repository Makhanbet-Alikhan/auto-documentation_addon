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
            "Операционные пользователи (исполнители) — сотрудники, ежедневно "
            "работающие с записями: создают, редактируют и закрывают задачи / "
            "события / документы. Достаточно базовых навыков работы с браузером.\n"
            "Администраторы — IT-специалисты или руководители, управляющие "
            "настройками и правами доступа. Требуется понимание ролевой модели Odoo."
        )

    @staticmethod
    def _default_scope(system_name, platform):
        return (
            "Руководство распространяется на работу с модулем %(sys)s, "
            "развёрнутым на платформе %(plat)s.\n"
            "Документ охватывает операции, доступные рядовому пользователю: "
            "просмотр списков, создание и редактирование записей, выполнение "
            "бизнес-операций через кнопки действий.\n"
            "Настройка системы, управление правами и технические параметры "
            "в данном документе не рассматриваются."
        ) % {"sys": system_name, "plat": platform}

    @staticmethod
    def _default_purpose(system_name):
        return (
            "Настоящий документ является руководством пользователя для работы "
            "с %(sys)s. Цель документа — обеспечить пользователей исчерпывающей "
            "информацией о порядке выполнения всех операций в системе, чтобы "
            "самостоятельно и эффективно использовать её в повседневной работе "
            "без обращения к разработчикам."
        ) % {"sys": system_name}

    @staticmethod
    def _default_conventions():
        return (
            "Жирный текст — названия кнопок, пунктов меню и полей формы.\n"
            "Курсив — важные термины при первом упоминании.\n"
            "Моноширинный текст — технические названия (модели, поля).\n"
            "[Скриншот] — место, куда вставляется снимок экрана.\n"
            "Шаги пронумерованы в порядке выполнения.\n"
            "⚠ Предупреждение — действие необратимо или требует внимания."
        )

    @staticmethod
    def _default_content_purpose(system_name):
        return (
            "Данный раздел описывает назначение %(sys)s и объясняет, какие "
            "бизнес-задачи решает система. Ознакомьтесь с ним перед началом "
            "работы, чтобы понять, для чего предназначен каждый раздел."
        ) % {"sys": system_name}

    @staticmethod
    def _default_materials():
        return (
            "Для работы с системой необходимы:\n"
            "Компьютер или ноутбук с доступом в интернет.\n"
            "Браузер Google Chrome, Mozilla Firefox или Microsoft Edge "
            "(рекомендуется последняя версия).\n"
            "Учётная запись в системе с назначенными правами доступа.\n"
            "Стабильное интернет-соединение."
        )

    @staticmethod
    def _default_preparation():
        return (
            "Откройте браузер и перейдите по адресу системы.\n"
            "Введите логин (адрес электронной почты) и пароль.\n"
            "Нажмите кнопку «Войти».\n"
            "Убедитесь, что в правом верхнем углу отображается ваше имя.\n"
            "Перейдите в нужный модуль через главное меню."
        )

    @staticmethod
    def _default_bibliography(platform):
        return (
            "%s — официальная документация: https://www.odoo.com/documentation\n"
            "Внутренние регламенты и инструкции компании."
        ) % platform

    @staticmethod
    def _default_glossary():
        return (
            "Запись — единица хранения данных (строка таблицы).\n"
            "Форма — экран для просмотра и редактирования одной записи.\n"
            "Список (kanban / tree) — экран с несколькими записями.\n"
            "Действие — кнопка, изменяющая статус или запускающая процесс.\n"
            "Фильтр — условие отбора записей в списке.\n"
            "Группировка — объединение записей по общему полю.\n"
            "Связанная запись (Many2one) — поле, ссылающееся на другую таблицу.\n"
            "Вложенный список (One2many) — список дочерних записей внутри формы."
        )

    # ------------------------------------------------------------------
    # Field step helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _field_label(fname, fmeta):
        """Return the best human-readable label for a field."""
        if isinstance(fmeta, dict):
            label = fmeta.get("string") or fmeta.get("label") or ""
        else:
            label = str(fmeta)
        return label.strip() or fname

    @staticmethod
    def _field_type_hint(fmeta):
        """Return a short Russian description of the field type."""
        if isinstance(fmeta, dict):
            ftype = fmeta.get("type") or fmeta.get("ttype") or ""
        else:
            ftype = ""
        return _TYPE_HINTS.get(ftype, "")

    @staticmethod
    def _field_required(fmeta):
        if isinstance(fmeta, dict):
            return bool(fmeta.get("required"))
        return False

    @staticmethod
    def _selection_options(fmeta):
        """Return list of (value, label) tuples for selection fields."""
        if not isinstance(fmeta, dict):
            return []
        sel = fmeta.get("selection") or []
        if callable(sel):
            return []
        return [(str(v), str(l)) for v, l in sel if l]

    def _field_step_lines(self, fields_meta):
        """
        Build a list of step strings — one per user-editable field.

        Each step describes EXACTLY what the user should fill in:
          «Заполните поле «<Название>» (<тип>) — <подсказка/описание>.»

        Required fields are marked with «(обязательно)».
        Selection fields list available options.
        The steps are sorted: required fields first, then optional.
        """
        if not fields_meta:
            return []

        lines_req = []
        lines_opt = []

        items = (
            fields_meta.items()
            if isinstance(fields_meta, dict)
            else [(r.get("name", ""), r) for r in fields_meta]
        )

        for fname, fmeta in items:
            if fname in _SYSTEM_FIELDS:
                continue
            if fname.startswith("_"):
                continue
            # skip non-stored computes
            if isinstance(fmeta, dict):
                if fmeta.get("compute") and not fmeta.get("store"):
                    continue
                if fmeta.get("readonly") and not fmeta.get("required"):
                    continue

            label = self._field_label(fname, fmeta)
            type_hint = self._field_type_hint(fmeta)
            required = self._field_required(fmeta)
            help_text = ""
            if isinstance(fmeta, dict):
                help_text = (fmeta.get("help") or fmeta.get("description") or "").strip()

            # Build step text
            parts = ['Заполните поле «%s»' % label]
            if type_hint:
                parts.append("(%s)" % type_hint)
            if required:
                parts.append("(обязательно)")

            # For selection — show options
            options = self._selection_options(fmeta)
            if options:
                opt_str = ", ".join('«%s»' % lbl for _, lbl in options[:8])
                if len(options) > 8:
                    opt_str += " и др."
                parts.append("— доступные значения: %s" % opt_str)
            elif help_text:
                # truncate long help texts
                hint = help_text[:120]
                if len(help_text) > 120:
                    hint += "…"
                parts.append("— %s" % hint)
            else:
                parts.append("— укажите значение")

            line = " ".join(parts) + "."
            if required:
                lines_req.append(line)
            else:
                lines_opt.append(line)

        return lines_req + lines_opt

    # ------------------------------------------------------------------
    # Section-6: function builders
    # ------------------------------------------------------------------
    @api.model
    def function_for_menu(self, menu, number):
        """Return a dict for a «View list» function derived from a menu node."""
        menu_name = menu.name or "Раздел"
        res_model = menu.res_model or ""
        view_modes = [v.strip() for v in (menu.view_modes or "").split(",") if v.strip()]

        # --- description ---
        if res_model:
            description = (
                "Данная функция открывает раздел «%(menu)s», в котором отображаются "
                "все доступные пользователю записи модели %(model)s. "
                "Записи можно просматривать, фильтровать, сортировать и группировать."
            ) % {"menu": menu_name, "model": res_model}
        else:
            description = (
                "Данная функция открывает раздел «%(menu)s». "
                "Пользователь получает доступ к списку элементов и может "
                "перемещаться по разделам системы."
            ) % {"menu": menu_name}

        # --- requirements ---
        groups = menu.groups_info or ""
        if groups:
            requirements = (
                "Для выполнения функции пользователь должен иметь одну из следующих "
                "ролей или групп доступа: %s." % groups
            )
        else:
            requirements = (
                "Пользователь должен быть авторизован в системе и иметь права "
                "на просмотр раздела «%s»." % menu_name
            )

        # --- steps ---
        steps_list = [
            "Откройте главное меню системы.",
            "Перейдите по пути: %s." % (menu.complete_name or menu_name),
        ]
        if "kanban" in view_modes:
            steps_list.append(
                "Система отобразит записи в виде канбан-доски. "
                "Карточки распределены по колонкам в соответствии со статусом."
            )
        elif "tree" in view_modes or "list" in view_modes:
            steps_list.append(
                "Система отобразит список записей в табличном виде. "
                "Для поиска используйте строку поиска в верхней части страницы."
            )
        else:
            steps_list.append("Система отобразит содержимое раздела.")

        steps_list += [
            "Для поиска нужной записи воспользуйтесь фильтрами и группировкой.",
            "Нажмите на запись, чтобы открыть её детальную форму.",
        ]

        # --- result ---
        result = (
            "Пользователь видит список записей раздела «%s» "
            "и может перейти к любой из них для просмотра или редактирования."
        ) % menu_name

        return {
            "name": "Просмотр раздела «%s»" % menu_name,
            "description": description,
            "requirements": requirements,
            "steps": "\n".join(steps_list),
            "result": result,
        }

    @api.model
    def function_for_create(self, menu, number, fields_meta_override=None):
        """
        Return a dict for a «Create record» function.

        fields_meta_override — optional dict {fname: fmeta} that takes
        priority over menu.fields_meta_json.  Pass this when the stored
        fields_meta_json is empty but a live ORM lookup is available.
        This guarantees that steps are NEVER the single generic sentence.
        """
        menu_name = menu.name or "Раздел"
        res_model = menu.res_model or ""

        # Resolve fields: override > stored json > empty
        if fields_meta_override:
            fields_meta = fields_meta_override
        else:
            fields_meta = menu.fields_meta_json or {}

        # --- description ---
        if res_model:
            description = (
                "Данная функция позволяет создать новую запись в разделе «%(menu)s» "
                "(модель %(model)s). Пользователь заполняет форму и сохраняет запись."
            ) % {"menu": menu_name, "model": res_model}
        else:
            description = (
                "Данная функция позволяет создать новую запись в разделе «%s». "
                "Пользователь заполняет форму и сохраняет запись." % menu_name
            )

        # --- requirements ---
        groups = menu.groups_info or ""
        if groups:
            requirements = (
                "Для создания записи пользователь должен иметь права на создание "
                "в разделе «%(menu)s». Необходимые роли: %(groups)s."
            ) % {"menu": menu_name, "groups": groups}
        else:
            requirements = (
                "Пользователь должен быть авторизован и иметь право на создание "
                "записей в разделе «%s»." % menu_name
            )

        # --- steps with real per-field lines ---
        field_steps = self._field_step_lines(fields_meta)

        steps_list = [
            "Перейдите в раздел «%s»." % (menu.complete_name or menu_name),
            "Нажмите кнопку «Создать» (или «New») в верхнем левом углу списка.",
            "Откроется форма создания новой записи.",
        ]

        if field_steps:
            steps_list.extend(field_steps)
        else:
            # absolute last resort — still better than nothing
            steps_list.append(
                "Заполните все доступные поля формы согласно требованиям бизнеса."
            )

        steps_list += [
            "Проверьте корректность введённых данных.",
            "Нажмите кнопку «Сохранить» (или перейдите на другую страницу — "
            "система сохранит запись автоматически).",
        ]

        # --- result ---
        result = (
            "Новая запись создана и отображается в списке раздела «%s». "
            "Система присваивает ей уникальный идентификатор."
        ) % menu_name

        return {
            "name": "Создание записи в разделе «%s»" % menu_name,
            "description": description,
            "requirements": requirements,
            "steps": "\n".join(steps_list),
            "result": result,
        }

    @api.model
    def function_for_inherited_create(
        self, base_model, module_name, number, introspector
    ):
        """
        Generate a «Create <base_model record>» function for a model
        that is extended via _inherit but NOT owned by this addon.

        Example: dpf_events extends event.event — there is no dedicated
        creation menu in dpf_events, so build_functions_from_menus() skips
        it.  This method fills that gap.

        :param base_model:   technical model name, e.g. 'event.event'
        :param module_name:  human name of the documenting addon
        :param number:       function sequence number
        :param introspector: doc.introspector singleton for live ORM lookup
        :return: dict ready to pass to doc.function.create()
        """
        # Human-readable model name from ir.model
        try:
            ir_model = introspector.env["ir.model"].search(
                [("model", "=", base_model)], limit=1
            )
            model_label = ir_model.name if ir_model else base_model
        except Exception:
            model_label = base_model

        # Live ORM field introspection
        try:
            fields_meta = introspector.get_user_input_fields(base_model)
        except Exception:
            fields_meta = {}

        # --- description ---
        description = (
            "Данная функция описывает создание новой записи «%(label)s» "
            "(модель %(model)s). Модуль «%(addon)s» расширяет данную модель, "
            "добавляя дополнительные поля и логику, однако форма создания "
            "открывается через стандартный раздел системы Odoo."
        ) % {"label": model_label, "model": base_model, "addon": module_name}

        # --- requirements ---
        requirements = (
            "Пользователь должен иметь права на создание записей типа «%s» "
            "в базовом модуле Odoo. Дополнительные поля, добавленные модулем «%s», "
            "отображаются на той же форме и заполняются по необходимости."
        ) % (model_label, module_name)

        # --- steps with real per-field lines ---
        field_steps = self._field_step_lines(fields_meta)

        steps_list = [
            "Откройте стандартный раздел Odoo, содержащий записи «%s»." % model_label,
            "Нажмите кнопку «Создать» (или «New»).",
            "Откроется форма создания новой записи «%s»." % model_label,
        ]

        if field_steps:
            steps_list.extend(field_steps)
        else:
            steps_list.append(
                "Заполните поля формы: обязательные поля отмечены звёздочкой (*)."
            )

        steps_list += [
            "Обратите внимание на дополнительные поля, добавленные модулем «%s» — "
            "они расположены в отдельной вкладке или секции формы." % module_name,
            "Проверьте введённые данные.",
            "Нажмите «Сохранить».",
        ]

        # --- result ---
        result = (
            "Создана новая запись «%(label)s». Дополнительные поля модуля «%(addon)s» "
            "сохранены вместе с базовой записью."
        ) % {"label": model_label, "addon": module_name}

        return {
            "name": "Создание записи «%s»" % model_label,
            "description": description,
            "requirements": requirements,
            "steps": "\n".join(steps_list),
            "result": result,
        }

    # ------------------------------------------------------------------
    # compose_menu_caption helper (used by doc_generation._build_menus)
    # ------------------------------------------------------------------
    @api.model
    def compose_menu_caption(self, menu_name, res_model, view_modes, fields_meta, groups):
        """Return a short caption sentence for a menu node."""
        if res_model:
            return (
                "Раздел «%(name)s» отображает записи %(model)s. "
                "Доступные режимы просмотра: %(modes)s."
            ) % {
                "name": menu_name,
                "model": res_model,
                "modes": ", ".join(view_modes or ["список"]),
            }
        return "Раздел «%s»." % menu_name
