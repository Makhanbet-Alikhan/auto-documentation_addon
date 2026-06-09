# -*- coding: utf-8 -*-
"""Generation orchestrator.

A ``doc.generation`` record is one run: the user picks one or more installed
modules, presses *Generate*, and this model coordinates the whole pipeline:

1. introspect the ORM (menus, actions, fields)         -> doc.introspector
2. parse the source code (docstrings, comments)         -> doc.source.parser
3. compose texts (deterministic, optional LLM)          -> services.text_composer
4. persist doc.module / doc.menu / doc.model.info
5. render Markdown / PDF / Word                         -> _render_markdown / report

Screenshots are NOT captured automatically. Placeholder text is inserted
instead so that a human can later paste real images from an external tool.
"""
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import text_composer

_logger = logging.getLogger(__name__)

# Placeholder shown in every output format instead of a real screenshot.
_SCREENSHOT_PLACEHOLDER = (
    "📌 [Здесь должен быть скриншот экрана «%s»]"
)


class DocGeneration(models.Model):
    _name = "doc.generation"
    _description = "Auto Doc - Generation Run"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(
        string="Название", required=True, default=lambda self: _("Новый запуск")
    )
    module_ids = fields.Many2many(
        "ir.module.module",
        string="Установленные модули",
        domain="[('state', '=', 'installed')]",
        help="Выберите один или несколько установленных модулей для документирования.",
    )
    module_names = fields.Char(
        string="Дополнительные модули",
        help="Необязательно. Технические названия через запятую, "
             "например: 'sale,my_addon'.",
    )
    state = fields.Selection(
        [
            ("draft", "Черновик"),
            ("collected", "Тексты собраны"),
            ("awaiting_shots", "Ожидает скриншоты"),
            ("done", "Готово"),
        ],
        string="Статус",
        default="draft",
        tracking=True,
    )
    doc_module_ids = fields.One2many(
        "doc.module", "generation_id", string="Задокументированные модули"
    )
    use_llm_caption = fields.Boolean(
        string="Использовать LLM-подписи",
        help="Если включено, внешний воркер может подписывать скриншоты через Vision LLM.",
    )

    # ------------------------------------------------------------------
    # Step 1-4: collect texts + structure
    # ------------------------------------------------------------------
    def _resolve_module_names(self):
        names = list(self.module_ids.mapped("name"))
        for raw in (self.module_names or "").split(","):
            raw = raw.strip()
            if raw and raw not in names:
                names.append(raw)
        return names

    def action_collect(self):
        """Собрать данные о модуле: меню, поля, описания."""
        self.ensure_one()
        modules = self._resolve_module_names()
        if not modules:
            raise UserError(_("Выберите хотя бы один модуль для документирования."))

        introspector = self.env["doc.introspector"]
        parser = self.env["doc.source.parser"]

        self.doc_module_ids.unlink()
        for module_name in modules:
            self._collect_one_module(module_name, introspector, parser)

        self.state = "awaiting_shots"
        return True

    def _collect_one_module(self, module_name, introspector, parser):
        """Build a single doc.module with its menus and models."""
        ir_module = self.env["ir.module.module"].search(
            [("name", "=", module_name)], limit=1
        )
        if not ir_module:
            raise UserError(_("Модуль '%s' не установлен.") % module_name)

        parsed = parser.parse_module(module_name)
        manifest = {
            "summary": ir_module.summary,
            "description": ir_module.description,
        }
        module_doc = text_composer.compose_module_description(
            manifest, parsed.get("module_docstring")
        )

        doc_module = self.env["doc.module"].create({
            "name": ir_module.shortdesc or module_name,
            "generation_id": self.id,
            "technical_name": module_name,
            "description": module_doc,
        })

        self._build_menus(doc_module, module_name, introspector)
        self._build_models(doc_module, module_name, introspector, parser, parsed)
        doc_module.apply_manual_defaults()
        doc_module.build_functions_from_menus()
        return doc_module

    def _build_menus(self, doc_module, module_name, introspector):
        """Create doc.menu records from the module's menu tree."""
        nodes = introspector.get_menu_tree(module_name)
        for node in nodes:
            res_model = node.get("res_model")
            fields_meta = introspector.get_fields_meta(res_model) if res_model else {}
            caption = text_composer.compose_menu_caption(
                node["name"], res_model, node.get("view_modes"), fields_meta
            )
            has_action = bool(node.get("web_url"))
            self.env["doc.menu"].create({
                "doc_module_id": doc_module.id,
                "menu_xmlid": node.get("complete_name"),
                "name": node["name"],
                "complete_name": node.get("complete_name"),
                "sequence": node.get("sequence", 10),
                "odoo_menu_id": node.get("menu_id"),
                "action_id": node.get("action_id") or 0,
                "res_model": res_model or False,
                "view_modes": ",".join(node.get("view_modes") or []),
                "web_url": node.get("web_url") or False,
                "caption": caption,
                # Never auto-capture: screenshots are inserted manually.
                "capture_state": "skipped",
            })

    def _build_models(self, doc_module, module_name, introspector, parser, parsed):
        """Create doc.model.info records with merged field tables."""
        for minfo in introspector.get_module_models(module_name):
            res_model = minfo["model"]
            fields_meta = introspector.get_fields_meta(res_model)
            class_doc = self._guess_class_doc(parsed, res_model)
            field_comments = self._guess_field_comments(parsed, res_model)
            rows = text_composer.compose_field_table_rows(fields_meta, field_comments)
            description = text_composer.compose_model_description(
                res_model, class_doc, {r["name"]: r["help"] for r in rows}
            )
            self.env["doc.model.info"].create({
                "doc_module_id": doc_module.id,
                "technical_name": res_model,
                "display_name": minfo.get("name"),
                "description": description,
                "field_table_json": rows,
                "field_count": len(rows),
            })

    @staticmethod
    def _guess_class_doc(parsed, res_model):
        candidate = "".join(p.capitalize() for p in res_model.replace(".", "_").split("_"))
        classes = (parsed or {}).get("classes", {})
        if candidate in classes:
            return classes[candidate]
        for doc in classes.values():
            if doc and res_model in doc:
                return doc
        return None

    @staticmethod
    def _guess_field_comments(parsed, res_model):
        candidate = "".join(p.capitalize() for p in res_model.replace(".", "_").split("_"))
        prefix = "%s." % candidate
        out = {}
        for fkey, comment in (parsed or {}).get("field_comments", {}).items():
            if fkey.startswith(prefix):
                out[fkey[len(prefix):]] = comment
        return out

    # ------------------------------------------------------------------
    # Markdown rendering — user-friendly style
    # ------------------------------------------------------------------
    @api.model
    def _render_markdown(self, doc_module):
        """Рендер одного doc.module в Markdown в стиле руководства пользователя."""
        lines = []
        title = doc_module.name or doc_module.technical_name

        # ---- Титул ----
        lines.append("# Руководство пользователя")
        lines.append("## %s" % title)
        if doc_module.system_name:
            lines.append("> %s" % doc_module.system_name)
        lines.append("> Версия: %s" % (doc_module.manual_version or "1.0"))
        if doc_module.developer:
            lines.append("> Разработчик: %s" % doc_module.developer)
        if doc_module.city_year:
            lines.append("> %s" % doc_module.city_year)
        lines.append("")

        # ---- 1. Введение ----
        lines.append("## 1. Введение")
        if doc_module.intro_user_categories:
            lines.append("### 1.1 Описание категорий пользователей")
            for line in doc_module.intro_user_categories.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")
        if doc_module.intro_scope:
            lines.append("### 1.2 Область применения")
            for line in doc_module.intro_scope.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")
        if doc_module.intro_purpose:
            lines.append("### 1.3 Назначение документа")
            lines.append(doc_module.intro_purpose.strip())
            lines.append("")
        if doc_module.intro_conventions:
            lines.append("### 1.4 Соглашения")
            for line in doc_module.intro_conventions.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        # ---- 2. Содержание документа ----
        lines.append("## 2. Содержание документа")
        if doc_module.content_purpose:
            lines.append("### 2.1 Назначение")
            lines.append(doc_module.content_purpose.strip())
            lines.append("")
        if doc_module.content_materials:
            lines.append("### 2.2 Материалы")
            for line in doc_module.content_materials.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")
        if doc_module.content_preparation:
            lines.append("### 2.3 Подготовка к работе")
            for i, line in enumerate(
                [l for l in doc_module.content_preparation.splitlines() if l.strip()], 1
            ):
                lines.append("%d. %s" % (i, line.strip()))
            lines.append("")

        # ---- 3. Список функций ----
        lines.append("## 3. Список функций")
        lines.append("")
        for func in doc_module.function_ids:
            lines.append("### Функция %d: %s" % (func.number or 0, func.name or ""))
            lines.append("")
            if func.description:
                lines.append("**Описание:** %s" % func.description.strip())
                lines.append("")
            if func.requirements:
                lines.append("**Требования:** %s" % func.requirements.strip())
                lines.append("")
            steps = func.step_lines() if hasattr(func, "step_lines") else []
            if steps:
                lines.append("**Порядок выполнения:**")
                for i, step in enumerate(steps, 1):
                    lines.append("%d. %s" % (i, step))
                lines.append("")
            # Screenshot placeholder — no auto-capture.
            lines.append("> %s" % (_SCREENSHOT_PLACEHOLDER % (func.name or "")))
            lines.append("")
            if func.result:
                lines.append("**Результат:** %s" % func.result.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

        # ---- Таблицы полей (приложение) ----
        if doc_module.model_ids:
            lines.append("## Приложение. Описание полей")
            lines.append("")
            for model in doc_module.model_ids:
                lines.append(
                    "### %s — %s" % (
                        model.display_name or model.technical_name,
                        "`%s`" % model.technical_name,
                    )
                )
                if model.description:
                    lines.append(model.description.strip())
                    lines.append("")
                rows = model.field_table_json or []
                if rows:
                    lines.append("| Поле | Название | Тип | Обязательное | Описание |")
                    lines.append("|------|-------|-----|-----------|---------|")
                    for r in rows:
                        lines.append("| `%s` | %s | %s | %s | %s |" % (
                            r.get("name", ""),
                            r.get("label", ""),
                            r.get("type", ""),
                            "Да" if r.get("required") else "",
                            (r.get("help") or "").replace("\n", " ").replace("|", "\\|"),
                        ))
                    lines.append("")

        # ---- 4. Литература ----
        if doc_module.bibliography:
            lines.append("## 4. Литература")
            for line in doc_module.bibliography.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        # ---- 5. Словарь ----
        if doc_module.glossary:
            lines.append("## 5. Словарь терминов")
            for line in doc_module.glossary.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        return "\n".join(lines)

    def action_render_all(self):
        """Рендер Markdown для каждого задокументированного модуля."""
        self.ensure_one()
        for doc_module in self.doc_module_ids:
            doc_module.markdown = self._render_markdown(doc_module)
        self.state = "done"
        return True

    def action_capture_screenshots(self):
        """Кнопка оставлена для совместимости. Скриншоты загружаются вручную."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Скриншоты"),
                "message": _(
                    "Автоматический захват отключён. Загрузите скриншоты вручную: "
                    "откройте задокументированный модуль ➜ вкладка «Функции» ➜ "
                    "откройте функцию ➜ поле «Скриншот»."
                ),
                "type": "info",
                "sticky": True,
            },
        }

    def action_print_pdf(self):
        """Печать PDF-отчёта для задокументированных модулей."""
        self.ensure_one()
        for doc_module in self.doc_module_ids:
            doc_module.refresh_function_screenshots()
        return self.env.ref("dpf_docs.action_report_doc_module").report_action(
            self.doc_module_ids
        )

    def action_download_word(self):
        """Сгенерировать Word-документ для всех задокументированных модулей."""
        self.ensure_one()
        if not self.doc_module_ids:
            raise UserError_("Нечего экспортировать. Сначала выполните сбор текстов."))
        for doc_module in self.doc_module_ids:
            doc_module.refresh_function_screenshots()
        data = self.env["doc.word.export"].build_docx(self.doc_module_ids)
        filename = "%s.docx" % (self.name or "documentation").replace(" ", "_")
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(data),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document",
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    def get_worker_spec(self):
        """Return the JSON spec the Playwright worker consumes (legacy)."""
        self.ensure_one()
        modules = []
        for doc_module in self.doc_module_ids:
            modules.append({
                "doc_module_id": doc_module.id,
                "technical_name": doc_module.technical_name,
                "tasks": doc_module.pending_screenshot_tasks(),
            })
        return {
            "generation_id": self.id,
            "use_llm_caption": self.use_llm_caption,
            "modules": modules,
        }

    @api.model
    def _cron_dispatch_pending(self):
        """Cron: log pending generations (no browser is started)."""
        pending = self.search([("state", "=", "awaiting_shots")])
        for gen in pending:
            remaining = sum(len(m.pending_screenshot_tasks()) for m in gen.doc_module_ids)
            if remaining:
                _logger.info(
                    "Generation %s has %s screenshot task(s) awaiting manual upload.",
                    gen.id, remaining,
                )
        return True
