# -*- coding: utf-8 -*-
"""Generation orchestrator.

Coordinates the full documentation pipeline:
  1. collect  — introspect ORM, parse source, build doc.module / menus / models
  2. enrich   — optionally overlay project task data from a snapshot set
  3. render   — produce Markdown and Word export

User-orientation improvements (v7)
-----------------------------------
* Primary model is detected first and drives the module description.
* Business-logic section (workflow states, action buttons) is generated
  for every model and rendered in the Word/PDF output.
* System fields, computed fields, and readonly fields are excluded from
  ALL user-facing sections (field table, menu captions, function steps).
* Access groups are collected from menus and rendered as «Доступен для:».
* The Appendix field table is sorted: required fields first, then optional.
"""
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import text_composer

_logger = logging.getLogger(__name__)

_SCREENSHOT_PLACEHOLDER = (
    "\U0001f4cc [Здесь должен быть скриншот экрана «%s»]"
)


class DocGeneration(models.Model):
    _name = "doc.generation"
    _description = "Auto Doc - Generation Run"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(
        string="Название",
        required=True,
        default=lambda self: _("Новый запуск"),
    )
    module_ids = fields.Many2many(
        "ir.module.module",
        string="Установленные модули",
        domain="[('state', '=', 'installed')]",
    )
    module_names = fields.Char(
        string="Дополнительные модули",
        help="Необязательно. Технические названия через запятую.",
    )
    state = fields.Selection(
        [
            ("draft",          "Черновик"),
            ("collected",      "Тексты собраны"),
            ("awaiting_shots", "Ожидает скриншоты"),
            ("done",           "Готово"),
        ],
        string="Статус",
        default="draft",
        tracking=True,
    )
    doc_module_ids = fields.One2many(
        "doc.module", "generation_id",
        string="Задокументированные модули",
    )
    use_llm_caption = fields.Boolean(
        string="Использовать LLM-подписи",
    )
    snapshot_set_id = fields.Many2one(
        "doc.project.snapshot.set",
        string="Project Snapshot Set",
        ondelete="set null",
        help=(
            "Необязательно. "
            "Выберите глобальный снапшот задач проекта. "
            "Если пусто — обогащение пропускается."
        ),
    )

    @api.onchange("module_ids", "module_names")
    def _onchange_auto_name(self):
        default_names = {_("Новый запуск"), "New Run", ""}
        if self.name and self.name not in default_names:
            return
        parts = []
        for mod in self.module_ids:
            parts.append(mod.shortdesc or mod.name)
        for raw in (self.module_names or "").split(","):
            raw = raw.strip()
            if raw and raw not in parts:
                parts.append(raw)
        if parts:
            self.name = ", ".join(parts)

    def _resolve_module_names(self):
        names = list(self.module_ids.mapped("name"))
        for raw in (self.module_names or "").split(","):
            raw = raw.strip()
            if raw and raw not in names:
                names.append(raw)
        return names

    # ------------------------------------------------------------------
    # Step 1: collect
    # ------------------------------------------------------------------
    def action_collect(self):
        self.ensure_one()
        modules = self._resolve_module_names()
        if not modules:
            raise UserError(_("Выберите хотя бы один модуль для документирования."))

        introspector = self.env["doc.introspector"]
        parser = self.env["doc.source.parser"]

        self.doc_module_ids.unlink()
        for module_name in modules:
            self._collect_one_module(module_name, introspector, parser)

        if self.snapshot_set_id:
            enricher = self.env["doc.project.enricher"]
            for doc_module in self.doc_module_ids:
                enricher.enrich_module(doc_module)

        self.state = "awaiting_shots"
        return True

    def action_enrich_all_modules(self):
        """Run enrichment on all modules without re-running Collect."""
        self.ensure_one()
        if not self.snapshot_set_id:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Project Snapshot"),
                    "message": _("Выберите Project Snapshot Set в поле выше."),
                    "type": "warning",
                    "sticky": True,
                },
            }
        if not self.doc_module_ids:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Project Snapshot"),
                    "message": _("Сначала выполните '1. Collect Texts'."),
                    "type": "warning",
                    "sticky": True,
                },
            }
        enricher = self.env["doc.project.enricher"]
        total_funcs = total_menus = 0
        for doc_module in self.doc_module_ids:
            stats = enricher.enrich_module(doc_module, overwrite=False)
            total_funcs += stats.get("functions_enriched", 0)
            total_menus += stats.get("menus_enriched", 0)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Обогащение завершено"),
                "message": _(
                    "Функций добавлено: %(f)s, меню обогащено: %(m)s."
                ) % {"f": total_funcs, "m": total_menus},
                "type": "success",
                "sticky": False,
            },
        }

    def _collect_one_module(self, module_name, introspector, parser):
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

        primary_model = introspector.get_primary_model(module_name)
        primary_model_doc = None
        if primary_model:
            primary_model_doc = self._guess_class_doc(parsed, primary_model)

        module_doc = text_composer.compose_module_description(
            manifest, primary_model_doc
        )

        doc_module = self.env["doc.module"].create({
            "name": ir_module.shortdesc or module_name,
            "generation_id": self.id,
            "technical_name": module_name,
            "description": module_doc,
            "primary_model": primary_model or False,
        })

        self._build_menus(doc_module, module_name, introspector)
        self._build_models(doc_module, module_name, introspector, parser, parsed, primary_model)
        doc_module.apply_manual_defaults()
        doc_module.build_functions_from_menus()
        return doc_module

    def _build_menus(self, doc_module, module_name, introspector):
        nodes = introspector.get_menu_tree(module_name)
        for node in nodes:
            res_model = node.get("res_model")
            fields_meta = introspector.get_user_input_fields(res_model) if res_model else {}
            groups = node.get("groups") or []
            caption = text_composer.compose_menu_caption(
                node["name"], res_model, node.get("view_modes"), fields_meta, groups
            )
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
                "capture_state": "skipped",
                "fields_meta_json": fields_meta if fields_meta else False,
                "groups_info": ", ".join(groups) if groups else False,
            })

    def _build_models(self, doc_module, module_name, introspector, parser, parsed, primary_model=None):
        for minfo in introspector.get_module_models(module_name):
            res_model = minfo["model"]
            user_fields_meta = introspector.get_user_input_fields(res_model)
            business_logic = introspector.get_business_logic(res_model)
            class_doc = self._guess_class_doc(parsed, res_model)
            field_comments = self._guess_field_comments(parsed, res_model)
            rows = text_composer.compose_field_table_rows(user_fields_meta, field_comments)
            rows.sort(key=lambda r: (0 if r.get("required") else 1, r.get("label", "")))
            description = text_composer.compose_model_description(
                res_model, class_doc, {r["name"]: r["help"] for r in rows}
            )
            biz_section = text_composer.compose_business_logic_section(
                business_logic, module_name
            )
            is_primary = (res_model == primary_model)
            self.env["doc.model.info"].create({
                "doc_module_id": doc_module.id,
                "technical_name": res_model,
                "display_name": minfo.get("name"),
                "description": description,
                "field_table_json": rows,
                "field_count": len(rows),
                "business_logic_text": biz_section or False,
                "is_primary": is_primary,
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
    # Markdown rendering
    # ------------------------------------------------------------------
    @api.model
    def _render_markdown(self, doc_module):
        lines = []
        title = doc_module.name or doc_module.technical_name

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

        lines.append("## 1. Введение")
        if doc_module.intro_user_categories:
            lines.append("### 1.1 Категории пользователей")
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
                [ln for ln in doc_module.content_preparation.splitlines() if ln.strip()], 1
            ):
                lines.append("%d. %s" % (i, line.strip()))
            lines.append("")

        primary_model_info = None
        for mi in doc_module.model_ids:
            if getattr(mi, "is_primary", False):
                primary_model_info = mi
                break
        if primary_model_info and getattr(primary_model_info, "business_logic_text", ""):
            lines.append("## 2.4 Как работает система")
            lines.append(primary_model_info.business_logic_text.strip())
            lines.append("")

        lines.append("## 3. Список функций")
        lines.append("")
        for func in doc_module.function_ids:
            lines.append("### Функция %d: %s" % (func.number or 0, func.name or ""))
            lines.append("")
            if func.description:
                lines.append("Описание: %s" % func.description.strip())
                lines.append("")
            if func.requirements:
                lines.append("Требования: %s" % func.requirements.strip())
                lines.append("")
            steps = [ln.strip() for ln in (func.steps or "").splitlines() if ln.strip()]
            if steps:
                lines.append("Порядок выполнения:")
                for i, step in enumerate(steps, 1):
                    lines.append("%d. %s" % (i, step))
                lines.append("")
            lines.append("> %s" % (_SCREENSHOT_PLACEHOLDER % (func.name or "")))
            lines.append("")
            if func.result:
                lines.append("Результат: %s" % func.result.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

        if doc_module.model_ids:
            lines.append("## Приложение. Описание полей")
            lines.append("")
            for model in doc_module.model_ids:
                rows = model.field_table_json or []
                if not rows:
                    continue
                lines.append(
                    "### %s — `%s`" % (
                        model.display_name or model.technical_name,
                        model.technical_name,
                    )
                )
                if model.description:
                    lines.append(model.description.strip())
                    lines.append("")
                if getattr(model, "business_logic_text", ""):
                    lines.append(model.business_logic_text.strip())
                    lines.append("")
                lines.append("| Поле | Название | Тип | Обязательное | Описание |")
                lines.append("|------|---------|-----|:---:|---------|")
                for r in rows:
                    lines.append("| `%s` | %s | %s | %s | %s |" % (
                        r.get("name", ""),
                        r.get("label", ""),
                        r.get("type", ""),
                        "✓" if r.get("required") else "",
                        (r.get("help") or "").replace("\n", " ").replace("|", "\\|"),
                    ))
                lines.append("")

        if doc_module.bibliography:
            lines.append("## 4. Литература")
            for line in doc_module.bibliography.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        if doc_module.glossary:
            lines.append("## 5. Словарь терминов")
            for line in doc_module.glossary.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        return "\n".join(lines)

    def action_render_all(self):
        self.ensure_one()
        for doc_module in self.doc_module_ids:
            doc_module.markdown = self._render_markdown(doc_module)
        self.state = "done"
        return True

    def action_capture_screenshots(self):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Скриншоты"),
                "message": _(
                    "Автоматический захват отключён. "
                    "Загрузите скриншоты вручную."
                ),
                "type": "info",
                "sticky": True,
            },
        }

    def action_print_pdf(self):
        self.ensure_one()
        for doc_module in self.doc_module_ids:
            doc_module.refresh_function_screenshots()
        return self.env.ref("dpf_docs.action_report_doc_module").report_action(
            self.doc_module_ids
        )

    def action_download_word(self):
        self.ensure_one()
        if not self.doc_module_ids:
            raise UserError_("Нечего экспортировать."))
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
        pending = self.search([("state", "=", "awaiting_shots")])
        for gen in pending:
            remaining = sum(
                len(m.pending_screenshot_tasks()) for m in gen.doc_module_ids
            )
            if remaining:
                _logger.info(
                    "Generation %s has %s screenshot task(s) awaiting.",
                    gen.id, remaining,
                )
        return True
