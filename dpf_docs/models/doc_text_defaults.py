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
            "Операционные пользователи (исполнители) — рядовые сотрудники, "
            "выполняющие повседневные операции: создание, редактирование и "
            "просмотр записей. Достаточен базовый уровень работы с компьютером.\n"
            "Администраторы системы — технические специалисты, выполняющие "
            "настройку системы, управление пользователями и правами доступа."
        )

    @staticmethod
    def _default_scope(system_name, platform):
        return (
            "Версия программного обеспечения: %s на платформе %s.\n"
            "Среда работы: система работает в веб-браузере. Для корректной работы "
            "необходим один из следующих браузеров: Google Chrome (рекомендуется), "
            "Mozilla Firefox или Microsoft Edge актуальной версии.\n"
            "Требования к подключению: наличие доступа к интернету или к локальной "
            "сети предприятия, в которой развёрнута система.\n"
            "Минимальные требования к компьютеру: операционная система Windows, "
            "Linux или macOS; оперативная память — не менее 4 ГБ; свободное место "
            "на диске — не менее 10 ГБ." % (system_name, platform)
        )

    @staticmethod
    def _default_purpose(system_name):
        return (
            "Настоящее руководство предназначено для пользователей системы %s и "
            "содержит подробное описание всех функций модуля с пошаговыми "
            "инструкциями по их использованию. Документ поможет быстро освоить "
            "работу с системой даже без специальной технической подготовки." % system_name
        )

    @staticmethod
    def _default_conventions():
        return (
            "Жирный шрифт обозначает названия кнопок и элементов интерфейса, "
            "например: нажмите кнопку «Создать».\n"
            "Курсив обозначает названия разделов, вкладок и полей формы, "
            "например: перейдите на вкладку «Настройки».\n"
            "Знак ➜ обозначает переход по меню, например: "
            "Главное меню ➜ Продажи ➜ Заказы.\n"
            "Текст в рамке «📌 Скриншот» указывает место, где должно "
            "располагаться изображение экрана."
        )

    @staticmethod
    def _default_content_purpose(system_name):
        return (
            "В данном разделе описаны все пользовательские функции системы %s. "
            "Каждая функция представлена в едином формате: назначение функции, "
            "требования для доступа, пошаговый порядок выполнения действий и "
            "ожидаемый результат." % system_name
        )

    @staticmethod
    def _default_materials():
        return (
            "Учётные данные для входа в систему (логин и пароль) — "
            "предоставляются администратором системы.\n"
            "Компьютер или ноутбук с поддерживаемой операционной системой "
            "и установленным браузером.\n"
            "Подключение к интернету или корпоративной сети предприятия.\n"
            "Права доступа к необходимым разделам системы — назначаются "
            "администратором в соответствии с должностными обязанностями."
        )

    @staticmethod
    def _default_preparation():
        return (
            "Получить у администратора системы логин и пароль для входа.\n"
            "Убедиться, что ваш компьютер соответствует минимальным системным "
            "требованиям (см. раздел «Область применения»).\n"
            "Открыть браузер и перейти по адресу системы Odoo "
            "(например: http://ваш-сервер:8069).\n"
            "Войти в систему: ввести логин и пароль на странице входа и "
            "нажать кнопку «Войти».\n"
            "Убедиться, что в главном меню отображается нужный модуль. Если "
            "модуль недоступен — обратиться к администратору для назначения прав."
        )

    @staticmethod
    def _default_bibliography(platform):
        return (
            "Официальная документация платформы %s — "
            "https://www.odoo.com/documentation\n"
            "Документация библиотеки python-docx — "
            "https://python-docx.readthedocs.io\n"
            "Внутренние регламенты и технические спецификации организации." % platform
        )

    @staticmethod
    def _default_glossary():
        return (
            "Модуль — функциональный блок системы Odoo, реализующий "
            "набор связанных возможностей (например, модуль «Проекты», "
            "модуль «Продажи»).\n"
            "Запись — единица данных, хранимая в системе (например, "
            "один проект, один заказ, один контакт).\n"
            "Меню — пункт навигации в интерфейсе системы, при нажатии "
            "на который открывается определённый экран или раздел.\n"
            "Форма — экран для создания или редактирования одной записи.\n"
            "Список (List view) — экран, отображающий несколько записей "
            "в виде таблицы.\n"
            "Фильтр — инструмент для отбора нужных записей по заданным "
            "условиям (например, показать только активные проекты).\n"
            "Kanban — визуальное представление записей в виде карточек "
            "по столбцам-статусам.\n"
            "Вложение — файл (изображение, документ), прикреплённый к записи.\n"
            "Администратор — пользователь с максимальными правами доступа, "
            "ответственный за настройку системы и управление пользователями."
        )

    # ------------------------------------------------------------------
    # Internal helpers — field metadata processing
    # ------------------------------------------------------------------
    @staticmethod
    def _usable_fields(fields_meta):
        """Return a filtered, sorted list of (fname, fmeta) tuples.

        Removes system / technical fields that have no user-facing meaning
        and sorts required fields first, then by field name.
        """
        if not fields_meta:
            return []
        result = []
        for fname, fmeta in fields_meta.items():
            if fname in _SYSTEM_FIELDS:
                continue
            ftype = fmeta.get("type", "")
            # Skip computed-only / non-stored relational counters
            if ftype in ("one2many",):
                continue
            result.append((fname, fmeta))
        # Required first, then alphabetical by label
        result.sort(key=lambda x: (not x[1].get("required", False), x[1].get("string", x[0]).lower()))
        return result

    @staticmethod
    def _type_hint(ftype):
        return _TYPE_HINTS.get(ftype, ftype or "")

    @classmethod
    def _describe_fields_short(cls, fields_meta, max_fields=6):
        """Return a short prose sentence listing the most important fields.

        Used in the description paragraph of a function so each menu gets
        a unique, concrete sentence about what data it contains.
        """
        usable = cls._usable_fields(fields_meta)
        if not usable:
            return ""
        # Take only the most important (required first, limit to max_fields)
        chosen = usable[:max_fields]
        labels = [m.get("string", fn) for fn, m in chosen]
        if len(labels) == 1:
            return "Основное поле: «%s»." % labels[0]
        if len(labels) == 2:
            return "Ключевые поля: «%s» и «%s»." % (labels[0], labels[1])
        return "Ключевые поля: %s и «%s»." % (
            ", ".join("«%s»" % lbl for lbl in labels[:-1]),
            labels[-1],
        )

    @classmethod
    def _field_step_lines(cls, fields_meta):
        """Return a list of step-strings, one per visible user field.

        Each step describes the field label, its type and its help text
        (if available), making the steps fully specific to this model.
        Required fields are marked with (*).
        """
        usable = cls._usable_fields(fields_meta)
        if not usable:
            return [
                "Заполните все необходимые поля формы. "
                "Названия полей отображаются слева от каждого поля ввода."
            ]
        lines = []
        for fname, fmeta in usable:
            label = fmeta.get("string") or fname
            ftype = fmeta.get("type", "")
            required = fmeta.get("required", False)
            help_text = (fmeta.get("help") or "").strip()
            type_hint = cls._type_hint(ftype)
            req_marker = " (*)" if required else ""
            # Build a rich step line
            if help_text:
                # Trim long help texts to ~120 chars
                if len(help_text) > 120:
                    help_text = help_text[:117] + "..."
                lines.append(
                    "В поле «%s»%s (%s) — %s" % (label, req_marker, type_hint, help_text)
                )
            else:
                lines.append(
                    "Заполните поле «%s»%s (%s)." % (label, req_marker, type_hint)
                )
        return lines

    # ------------------------------------------------------------------
    # Per-function defaults — user-friendly step-by-step instructions
    # ------------------------------------------------------------------
    @api.model
    def function_for_menu(self, menu, number):
        """Return user-friendly doc.function values for one documented menu.

        Generates plain-language, click-by-click instructions adapted to
        the menu's view modes and actual model field metadata so every menu
        gets its own unique, concrete description.
        """
        title = menu.name or "Раздел"
        module_name = (
            menu.doc_module_id.name
            if menu.doc_module_id
            else "системы"
        )
        fields_meta = menu.fields_meta_json or {}
        res_model = menu.res_model or ""

        # --- Description: use caption if set, otherwise build from field metadata ---
        caption_val = (getattr(menu, "caption", None) or "").strip()
        if caption_val:
            description = caption_val
        elif fields_meta:
            field_summary = self._describe_fields_short(fields_meta)
            description = (
                "Раздел «%s» предназначен для работы с записями модели %s. "
                "Пользователь может просматривать, создавать, редактировать "
                "и удалять записи в рамках своих прав доступа. %s"
            ) % (title, res_model or title, field_summary)
        else:
            description = (
                "Данная функция открывает раздел «%s» и предоставляет "
                "пользователю доступ к соответствующим данным и операциям "
                "в системе %s." % (title, module_name)
            )

        requirements = (
            "Пользователь должен быть авторизован в системе.\n"
            "Пользователю должен быть предоставлен доступ к разделу «%s». "
            "При отсутствии доступа обратитесь к администратору системы." % title
        )

        view_modes = [v.strip() for v in (menu.view_modes or "").split(",") if v.strip()]
        steps = self._steps_for_menu(title, module_name, view_modes, menu, fields_meta)

        result = (
            "Экран раздела «%s» успешно открыт. "
            "Пользователю отображаются данные и доступны все действия "
            "в рамках его прав доступа." % title
        )

        screenshot_caption = "Экран раздела «%s»" % title

        return {
            "name": title,
            "description": description,
            "requirements": requirements,
            "steps": steps,
            "result": result,
            "screenshot_caption": screenshot_caption,
        }

    @classmethod
    def _steps_for_menu(cls, title, module_name, view_modes, menu, fields_meta=None):
        """Build detailed step-by-step instructions depending on view modes
        and the menu's actual field metadata.
        """
        has_list = "list" in view_modes or not view_modes
        has_form = "form" in view_modes
        has_kanban = "kanban" in view_modes
        has_pivot = "pivot" in view_modes
        has_graph = "graph" in view_modes
        has_calendar = "calendar" in view_modes
        fields_meta = fields_meta or {}

        lines = [
            "В главном меню системы найдите и нажмите на раздел «%s»." % module_name,
            "В открывшемся подменю выберите пункт «%s»." % title,
        ]

        if has_list:
            lines.append(
                "Откроется список записей раздела «%s» в виде таблицы. "
                "Каждая строка — одна запись." % title
            )
            lines.append(
                "Для поиска нужной записи воспользуйтесь строкой поиска "
                "в верхней части экрана: введите ключевое слово и нажмите "
                "клавишу Enter."
            )
            lines.append(
                "Чтобы отфильтровать записи по определённому критерию, "
                "нажмите кнопку «Фильтры» рядом со строкой поиска и "
                "выберите нужный фильтр из списка."
            )

        if has_kanban:
            lines.append(
                "Для переключения в режим Kanban (карточки по столбцам) "
                "нажмите на иконку Kanban в правом верхнем углу списка."
            )

        if has_form:
            lines.append(
                "Чтобы открыть конкретную запись для просмотра или "
                "редактирования, нажмите на её строку в списке."
            )
            if fields_meta:
                # List required fields specifically
                req_fields = [
                    m.get("string", fn)
                    for fn, m in cls._usable_fields(fields_meta)
                    if m.get("required")
                ]
                if req_fields:
                    lines.append(
                        "В открывшейся форме обязательно заполните поля: %s "
                        "(отмечены звёздочкой *)." %
                        ", ".join("«%s»" % f for f in req_fields)
                    )
                else:
                    lines.append(
                        "В открывшейся форме заполните нужные поля. "
                        "Поля, отмеченные звёздочкой (*), являются обязательными."
                    )
            else:
                lines.append(
                    "В открывшейся форме заполните или измените нужные поля. "
                    "Поля, отмеченные звёздочкой (*), являются обязательными."
                )
            # Manual key_fields override
            key_fields = (getattr(menu, "key_fields", None) or "").strip()
            if key_fields:
                lines.append(
                    "Обратите особое внимание на ключевые поля формы: %s."
                    % key_fields
                )
            lines.append(
                "После внесения изменений нажмите кнопку «Сохранить» "
                "(значок облака или кнопка в верхнем левом углу). "
                "Чтобы отменить изменения — нажмите «Отменить»."
            )

        if has_pivot or has_graph:
            lines.append(
                "Для просмотра аналитических данных переключитесь в режим "
                "«Сводная таблица» или «График», нажав на соответствующую "
                "иконку в правом верхнем углу экрана."
            )

        if has_calendar:
            lines.append(
                "Для просмотра записей в формате календаря переключитесь "
                "в режим «Календарь», нажав на иконку календаря в правом "
                "верхнем углу экрана."
            )

        lines.append(
            "Чтобы создать новую запись, нажмите кнопку «Создать» "
            "(кнопка «New» в верхнем левом углу экрана)."
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Universal create function — unique per model
    # ------------------------------------------------------------------
    @api.model
    def function_for_create(self, menu, number):
        """Generate a unique «Создание записи» function for any model/menu.

        The description, field steps and result sentence are all derived from
        the real ORM field metadata stored in ``menu.fields_meta_json``, so
        every menu produces a distinct, concrete set of instructions.
        """
        title = menu.name or "Раздел"
        module_name = (
            menu.doc_module_id.name if menu.doc_module_id else "системы"
        )
        fields_meta = menu.fields_meta_json or {}
        res_model = menu.res_model or ""

        record_label = self._singular_label(title)

        # Build description from field metadata when available
        if fields_meta:
            field_summary = self._describe_fields_short(fields_meta)
            description = (
                "Функция предназначена для создания новой записи в разделе «%s» "
                "системы %s (модель: %s). "
                "Пользователь заполняет форму, указывая все необходимые данные, "
                "после чего сохраняет запись в системе. %s"
            ) % (title, module_name, res_model or title, field_summary)
        else:
            description = (
                "Функция предназначена для создания новой записи в разделе «%s» "
                "системы %s. Пользователь заполняет форму, указывая все необходимые "
                "данные, после чего сохраняет запись в системе." % (title, module_name)
            )

        requirements = (
            "Пользователь должен быть авторизован в системе.\n"
            "Пользователю должен быть предоставлен доступ к разделу «%s» и "
            "право на создание новых записей. "
            "При отсутствии доступа обратитесь к администратору системы."
            % title
        )

        # Navigation steps
        steps = [
            "В главном меню системы найдите и нажмите на раздел «%s»." % module_name,
            "В открывшемся подменю выберите пункт «%s»." % title,
            "В открывшемся списке нажмите кнопку «New» (верхний левый угол экрана), "
            "чтобы перейти к форме создания новой записи.",
        ]

        # Per-field steps from real ORM metadata
        field_steps = self._field_step_lines(fields_meta)
        steps.extend(field_steps)

        steps += [
            "Поля, отмеченные звёздочкой (*), являются обязательными — "
            "их необходимо заполнить перед сохранением.",
            "После заполнения всех необходимых полей нажмите кнопку «Сохранить» "
            "(значок облака в верхнем левом углу формы).",
            "Новая запись появится в списке раздела «%s»." % title,
        ]

        return {
            "name": "Создание записи «%s»" % record_label,
            "description": description,
            "requirements": requirements,
            "steps": "\n".join(steps),
            "result": (
                "Новая запись «%s» успешно создана и сохранена в системе. "
                "Запись отображается в списке раздела «%s» и доступна "
                "для дальнейшего просмотра, редактирования и обработки."
                % (record_label, title)
            ),
            "screenshot_caption": "Форма создания записи «%s»" % record_label,
        }

    @staticmethod
    def _singular_label(menu_title):
        """Return a best-effort singular lowercase label from a menu title."""
        t = (menu_title or "").strip()
        if not t:
            return "запись"
        lower = t.lower()
        replacements = [
            ("ости", "ость"),
            ("оты", "ота"),
            ("ники", "ник"),
            ("ументы", "умент"),
            ("заявки", "заявка"),
            ("заказы", "заказ"),
            ("задачи", "задача"),
            ("события", "событие"),
            ("проекты", "проект"),
            ("записи", "запись"),
            ("статьи", "статья"),
            ("контакты", "контакт"),
            ("договоры", "договор"),
            ("счета", "счёт"),
            ("комнаты", "комната"),
            ("залы", "зал"),
            ("помещения", "помещение"),
            ("площадки", "площадка"),
            ("участники", "участник"),
            ("докладчики", "докладчик"),
            ("мероприятия", "мероприятие"),
            ("оборудование", "оборудование"),
            ("категории", "категория"),
            ("теги", "тег"),
        ]
        for plural, singular in replacements:
            if lower.endswith(plural):
                return t[: len(t) - len(plural)] + singular
        return lower

    # ------------------------------------------------------------------
    # news.post — dedicated create / edit function (legacy, kept for compat)
    # ------------------------------------------------------------------
    @api.model
    def function_for_news_create(self, menu, number):
        """Generate a dedicated 'Создание новости' function for news.post menus.

        Kept for backwards compatibility. Prefer function_for_create() for
        new modules — it now uses real ORM metadata automatically.
        """
        module_name = (
            menu.doc_module_id.name if menu.doc_module_id else "DPF News"
        )
        fields_meta = menu.fields_meta_json or {}
        field_help = self._news_post_field_help(menu)

        # Prefer ORM-driven field steps if metadata is available
        if fields_meta:
            field_steps = self._field_step_lines(fields_meta)
        else:
            field_steps = [
                "В поле «Title» введите заголовок новости — он будет отображаться "
                "на сайте и в списке публикаций.",
                "В поле «Publication Date» задайте дату публикации материала.",
                "На вкладке «Content» введите основной текст новости с помощью "
                "встроенного редактора.",
                "При необходимости добавьте изображения на вкладке «Images».",
                "При необходимости настройте параметры отображения галереи "
                "на вкладке «Gallery Settings».",
                "Если требуется автоматическая публикация во внешних каналах, "
                "включите переключатель «Auto-publish to Social Media».",
                "Поле «Social Status» отображает текущий статус отправки новости "
                "в социальные сети (например: Partially sent, Sent).",
                "Убедитесь, что переключатель «Is Published» включён, если новость "
                "должна быть видна на сайте.",
            ]

        steps = [
            "В главном меню системы найдите и нажмите на раздел «%s»." % module_name,
            "В открывшемся разделе нажмите кнопку «New» (верхний левый угол), "
            "чтобы создать новую новость.",
        ] + field_steps + [
            "Нажмите кнопку «Сохранить» (значок облака в верхнем левом углу), "
            "чтобы записать новость в систему.",
            "При необходимости нажмите кнопку «Open on Website» для проверки "
            "отображения новости на сайте.",
        ]

        if field_help:
            steps.append(
                "Сводка ключевых полей формы: %s." % field_help
            )

        return {
            "name": "Создание новости",
            "description": (
                "Функция предназначена для создания, редактирования и публикации "
                "новости в системе %s. Пользователь заполняет карточку новости, "
                "указывает дату публикации, основной текст, изображения и параметры "
                "размещения в системе и на сайте." % module_name
            ),
            "requirements": (
                "Пользователь должен быть авторизован в системе.\n"
                "Пользователю должен быть предоставлен доступ к разделу «%s» и "
                "право на создание или редактирование записей новостей. "
                "При отсутствии доступа обратитесь к администратору системы."
                % module_name
            ),
            "steps": "\n".join(steps),
            "result": (
                "Новая запись новости успешно создана и сохранена в системе. "
                "Новость отображается в разделе «All Posts» и, при включённом "
                "переключателе «Is Published», становится доступна на сайте "
                "и в подключённых каналах публикации."
            ),
            "screenshot_caption": "Экран создания и редактирования новости",
        }

    @staticmethod
    def _news_post_field_help(menu):
        """Return a readable summary of key news.post field purposes."""
        all_pairs = [
            ("title",                    "«Title» — заголовок новости, отображается на сайте"),
            ("publication date",         "«Publication Date» — дата публикации материала"),
            ("is published",             "«Is Published» — признак видимости новости на сайте"),
            ("can publish",              "«Can Publish» — право на публикацию для данного пользователя"),
            ("website url",              "«Website URL» — относительная ссылка на страницу новости"),
            ("website absolute url",     "«Website Absolute URL» — полный адрес страницы новости"),
            ("social status",            "«Social Status» — статус отправки новости в социальные сети"),
            ("auto-publish",             "«Auto-publish to Social Media» — автоматическая публикация в соцсети"),
            ("visible on current website", "«Visible on current website» — видимость на текущем сайте"),
        ]
        key_fields_lower = (getattr(menu, "key_fields", None) or "").lower()
        if key_fields_lower:
            found = [label for key, label in all_pairs if key in key_fields_lower]
        else:
            found = [label for _, label in all_pairs]
        return "; ".join(found)
