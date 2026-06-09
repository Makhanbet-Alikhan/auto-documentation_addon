# -*- coding: utf-8 -*-
"""Deterministic Russian-language manual text provider.

This abstract service centralises the boilerplate wording used by the
user-manual export so the PDF and Word builders stay focused on layout. The
texts intentionally mirror the structure of the reference manual (Введение,
Содержание документа, Список функций, ...). All texts are produced
deterministically; no external service is required.

The module being documented is Russian-language content, so these strings are
deliberately written in Russian even though the surrounding code and comments
are English.
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
            "Система предназначена для следующих категорий пользователей:\n"
            "Менеджеры: средний уровень практической подготовки; опыт ведения "
            "процессов и документации. Необходимы знания об операционных "
            "процессах и базовых принципах работы в системе.\n"
            "Аналитики: средний уровень подготовки; умение работать с данными, "
            "создавать отчёты и анализировать эффективность работы.\n"
            "Операционные пользователи (исполнители): базовый уровень; умение "
            "работать в системе и понимать её базовые функции.\n"
            "Администраторы системы: высокий уровень; настройка системы, "
            "управление пользователями и правами доступа."
        )

    @staticmethod
    def _default_scope(system_name, platform):
        return (
            "Версия программного обеспечения: %s на платформе %s.\n"
            "Окружение: программное обеспечение работает в веб-среде. Для "
            "корректной работы требуется браузер (Google Chrome, Mozilla "
            "Firefox, Microsoft Edge) и подключение к Интернету.\n"
            "Системные требования: операционная система Windows, Linux или "
            "macOS; ПК или ноутбук с доступом в Интернет, минимальная "
            "конфигурация: 4 ГБ оперативной памяти, 10 ГБ свободного места на "
            "диске." % (system_name, platform)
        )

    @staticmethod
    def _default_purpose(system_name):
        return (
            "Руководство предназначено для обеспечения пользователей полным "
            "описанием работы системы %s, включая ключевые функции и пошаговые "
            "инструкции по их использованию." % system_name
        )

    @staticmethod
    def _default_conventions():
        # Each line becomes a bullet; the export renders inline emphasis.
        return (
            "Жирный шрифт используется для выделения названий кнопок и важных "
            "действий, например: «Нажать кнопку 'Создать'».\n"
            "Курсив используется для выделения ключевых терминов."
        )

    @staticmethod
    def _default_content_purpose(system_name):
        return (
            "В данном разделе содержатся ссылки на функциональные возможности "
            "системы %s. Каждая функция представлена с подробным описанием, "
            "включая назначение, требования, порядок выполнения и результат."
            % system_name
        )

    @staticmethod
    def _default_materials():
        return (
            "Пароли и учётные данные — для входа в систему требуется логин и "
            "пароль, предоставляемые администратором.\n"
            "Компьютеры и периферийные устройства — ПК с поддерживаемой "
            "операционной системой.\n"
            "Интерфейсы и протоколы — необходимо подключение к Интернету."
        )

    @staticmethod
    def _default_preparation():
        return (
            "Получить системные пароли и доступ к нужным модулям.\n"
            "Убедиться в наличии достаточного дискового пространства.\n"
            "Проверить совместимость устройства с минимальными системными "
            "требованиями."
        )

    @staticmethod
    def _default_bibliography(platform):
        return (
            "Официальная документация платформы %s — https://www.odoo.com/documentation\n"
            "Внутренние регламенты и спецификации организации." % platform
        )

    @staticmethod
    def _default_glossary():
        return (
            "Модуль — функциональный блок системы, реализующий набор связанных "
            "возможностей.\n"
            "Меню — пункт навигации, открывающий экран системы.\n"
            "Запись — единица данных, хранимая в системе."
        )

    # ------------------------------------------------------------------
    # Per-function defaults (generated from a menu/screen)
    # ------------------------------------------------------------------
    @api.model
    def function_for_menu(self, menu, number):
        """Return default doc.function values for one documented menu."""
        title = menu.name or "Раздел"
        description = (
            menu.caption
            or ("Эта функция позволяет пользователю работать с разделом «%s» "
                "системы." % title)
        )
        requirements = (
            "У пользователя должны быть необходимые права доступа к разделу "
            "«%s»." % title
        )
        steps = (
            "Открыть главное меню системы.\n"
            "Перейти в раздел «%s».\n"
            "Выполнить необходимые действия на открывшемся экране." % title
        )
        result = (
            "Экран раздела «%s» открыт; пользователю доступны соответствующие "
            "данные и действия." % title
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
