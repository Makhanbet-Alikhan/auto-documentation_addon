# -*- coding: utf-8 -*-
"""Deterministic Russian-language manual text provider (user-friendly style).

Generates plain-language, step-by-step wording for every section of the
user manual so that a non-technical reader can understand how to use the
documented module without any developer knowledge.

All texts are written in Russian. The function_for_menu() method now generates
detailed, click-by-click instructions based on the menu's name, model and
view modes — ready for a real «Руководство пользователя».
"""
from odoo import api, models


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
    # Per-function defaults — user-friendly step-by-step instructions
    # ------------------------------------------------------------------
    @api.model
    def function_for_menu(self, menu, number):
        """Return user-friendly doc.function values for one documented menu.

        Generates plain-language, click-by-click instructions adapted to
        the menu's view modes and linked model so a non-technical user can
        follow them immediately.
        """
        title = menu.name or "Раздел"
        module_name = (
            menu.doc_module_id.name
            if menu.doc_module_id
            else "системы"
        )
        # Build context-aware description from caption or generic fallback.
        description = (
            menu.caption
            or (
                "Данная функция открывает раздел «%s» и предоставляет "
                "пользователю доступ к соответствующим данным и операциям "
                "в системе %s." % (title, module_name)
            )
        )

        requirements = (
            "Пользователь должен быть авторизован в системе.\n"
            "Пользователю должен быть предоставлен доступ к разделу «%s». "
            "При отсутствии доступа обратитесь к администратору системы." % title
        )

        # Build view-mode-aware steps.
        view_modes = [v.strip() for v in (menu.view_modes or "").split(",") if v.strip()]
        steps = self._steps_for_menu(title, module_name, view_modes, menu)

        result = (
            "Экран раздела «%s» успешно открыт. "
            "Пользователю отображаются данные и доступны все действия "
            "в рамках его прав доступа." % title
        )

        caption = "Экран раздела «%s»" % title

        return {
            "name": title,
            "description": description,
            "requirements": requirements,
            "steps": steps,
            "result": result,
            "screenshot_caption": caption,
        }

    @staticmethod
    def _steps_for_menu(title, module_name, view_modes, menu):
        """Build detailed step-by-step instructions depending on view modes."""
        has_list = "list" in view_modes or not view_modes
        has_form = "form" in view_modes
        has_kanban = "kanban" in view_modes
        has_pivot = "pivot" in view_modes
        has_graph = "graph" in view_modes
        has_calendar = "calendar" in view_modes

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
            lines.append(
                "В открывшейся форме заполните или измените нужные поля. "
                "Поля, отмеченные звёздочкой (*), являются обязательными."
            )
            # Mention key fields when available
            if menu.key_fields and menu.key_fields.strip():
                lines.append(
                    "Обратите особое внимание на ключевые поля формы: %s."
                    % menu.key_fields.strip()
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
    # news.post — dedicated create / edit function
    # ------------------------------------------------------------------
    @api.model
    def function_for_news_create(self, menu, number):
        """Generate a dedicated 'Создание новости' function for news.post menus.

        Called automatically from build_functions_from_menus() when the menu's
        res_model is 'news.post'. Produces a full click-by-click instruction
        set that explains every visible field on the news form.
        """
        module_name = (
            menu.doc_module_id.name if menu.doc_module_id else "DPF News"
        )
        field_help = self._news_post_field_help(menu)

        steps = [
            "В главном меню системы найдите и нажмите на раздел «%s»." % module_name,
            "В открывшемся разделе нажмите кнопку «New» (верхний левый угол), "
            "чтобы создать новую новость.",
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
        """Return a readable summary of key news.post field purposes.

        Scans the menu's key_fields string (if any) and returns the matching
        human-readable descriptions separated by semicolons. Falls back to the
        full default set when key_fields is empty.
        """
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

        key_fields_lower = (menu.key_fields or "").lower()
        if key_fields_lower:
            found = [label for key, label in all_pairs if key in key_fields_lower]
        else:
            # No key_fields stored — return the full default set
            found = [label for _, label in all_pairs]

        return "; ".join(found)
