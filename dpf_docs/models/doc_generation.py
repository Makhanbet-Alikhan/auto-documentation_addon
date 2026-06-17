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

_SCREENSHOT_PLACEHOLDER = (
    "\U0001f4cc [\u0417\u0434\u0435\u0441\u044c \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442 \u044d\u043a\u0440\u0430\u043d\u0430 \u00ab%s\u00bb]"
)


class DocGeneration(models.Model):
    _name = "doc.generation"
    _description = "Auto Doc - Generation Run"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(
        string="\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435",
        required=True,
        default=lambda self: _("\u041d\u043e\u0432\u044b\u0439 \u0437\u0430\u043f\u0443\u0441\u043a"),
    )
    module_ids = fields.Many2many(
        "ir.module.module",
        string="\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043d\u044b\u0435 \u043c\u043e\u0434\u0443\u043b\u0438",
        domain="[('state', '=', 'installed')]",
    )
    module_names = fields.Char(
        string="\u0414\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u043c\u043e\u0434\u0443\u043b\u0438",
        help="\u041d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e. \u0422\u0435\u0445\u043d\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f \u0447\u0435\u0440\u0435\u0437 \u0437\u0430\u043f\u044f\u0442\u0443\u044e, "
             "\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: 'sale,my_addon'.",
    )
    state = fields.Selection(
        [
            ("draft", "\u0427\u0435\u0440\u043d\u043e\u0432\u0438\u043a"),
            ("collected", "\u0422\u0435\u043a\u0441\u0442\u044b \u0441\u043e\u0431\u0440\u0430\u043d\u044b"),
            ("awaiting_shots", "\u041e\u0436\u0438\u0434\u0430\u0435\u0442 \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b"),
            ("done", "\u0413\u043e\u0442\u043e\u0432\u043e"),
        ],
        string="\u0421\u0442\u0430\u0442\u0443\u0441",
        default="draft",
        tracking=True,
    )
    doc_module_ids = fields.One2many(
        "doc.module", "generation_id",
        string="\u0417\u0430\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0435 \u043c\u043e\u0434\u0443\u043b\u0438",
    )
    use_llm_caption = fields.Boolean(
        string="\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c LLM-\u043f\u043e\u0434\u043f\u0438\u0441\u0438",
    )
    snapshot_set_id = fields.Many2one(
        "doc.project.snapshot.set",
        string="Project Snapshot Set",
        ondelete="set null",
        help=(
            "\u041d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e. "
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0433\u043b\u043e\u0431\u0430\u043b\u044c\u043d\u044b\u0439 \u0441\u043d\u0430\u043f\u0448\u043e\u0442 \u0437\u0430\u0434\u0430\u0447 \u043f\u0440\u043e\u0435\u043a\u0442\u0430 \u0434\u043b\u044f \u043e\u0431\u043e\u0433\u0430\u0449\u0435\u043d\u0438\u044f. "
            "\u0415\u0441\u043b\u0438 \u043f\u0443\u0441\u0442\u043e \u2014 \u043e\u0431\u043e\u0433\u0430\u0449\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u043f\u0443\u0441\u043a\u0430\u0435\u0442\u0441\u044f."
        ),
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_module_names(self):
        names = list(self.module_ids.mapped("name"))
        for raw in (self.module_names or "").split(","):
            raw = raw.strip()
            if raw and raw not in names:
                names.append(raw)
        return names

    # ------------------------------------------------------------------
    # Step 1-4: collect texts + structure
    # ------------------------------------------------------------------
    def action_collect(self):
        """\u0421\u043e\u0431\u0440\u0430\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435 \u043e \u043c\u043e\u0434\u0443\u043b\u0435: \u043c\u0435\u043d\u044e, \u043f\u043e\u043b\u044f, \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f."""
        self.ensure_one()
        modules = self._resolve_module_names()
        if not modules:
            raise UserError(_("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u0438\u043d \u043c\u043e\u0434\u0443\u043b\u044c \u0434\u043b\u044f \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f."))

        introspector = self.env["doc.introspector"]
        parser = self.env["doc.source.parser"]

        self.doc_module_ids.unlink()
        for module_name in modules:
            self._collect_one_module(module_name, introspector, parser)

        # Optional: enrich from project snapshot set
        if self.snapshot_set_id:
            enricher = self.env["doc.project.enricher"]
            for doc_module in self.doc_module_ids:
                enricher.enrich_module(doc_module)

        self.state = "awaiting_shots"
        return True

    def _collect_one_module(self, module_name, introspector, parser):
        ir_module = self.env["ir.module.module"].search(
            [("name", "=", module_name)], limit=1
        )
        if not ir_module:
            raise UserError(_("\u041c\u043e\u0434\u0443\u043b\u044c '%s' \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d.") % module_name)

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
        nodes = introspector.get_menu_tree(module_name)
        for node in nodes:
            res_model = node.get("res_model")
            fields_meta = introspector.get_fields_meta(res_model) if res_model else {}
            caption = text_composer.compose_menu_caption(
                node["name"], res_model, node.get("view_modes"), fields_meta
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
            })

    def _build_models(self, doc_module, module_name, introspector, parser, parsed):
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
    # Markdown rendering
    # ------------------------------------------------------------------
    @api.model
    def _render_markdown(self, doc_module):
        """\u0420\u0435\u043d\u0434\u0435\u0440 \u043e\u0434\u043d\u043e\u0433\u043e doc.module \u0432 Markdown."""
        lines = []
        title = doc_module.name or doc_module.technical_name

        lines.append("# \u0420\u0443\u043a\u043e\u0432\u043e\u0434\u0441\u0442\u0432\u043e \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f")
        lines.append("## %s" % title)
        if doc_module.system_name:
            lines.append("> %s" % doc_module.system_name)
        lines.append("> \u0412\u0435\u0440\u0441\u0438\u044f: %s" % (doc_module.manual_version or "1.0"))
        if doc_module.developer:
            lines.append("> \u0420\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u0447\u0438\u043a: %s" % doc_module.developer)
        if doc_module.city_year:
            lines.append("> %s" % doc_module.city_year)
        lines.append("")

        lines.append("## 1. \u0412\u0432\u0435\u0434\u0435\u043d\u0438\u0435")
        if doc_module.intro_user_categories:
            lines.append("### 1.1 \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0439 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439")
            for line in doc_module.intro_user_categories.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")
        if doc_module.intro_scope:
            lines.append("### 1.2 \u041e\u0431\u043b\u0430\u0441\u0442\u044c \u043f\u0440\u0438\u043c\u0435\u043d\u0435\u043d\u0438\u044f")
            for line in doc_module.intro_scope.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")
        if doc_module.intro_purpose:
            lines.append("### 1.3 \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430")
            lines.append(doc_module.intro_purpose.strip())
            lines.append("")
        if doc_module.intro_conventions:
            lines.append("### 1.4 \u0421\u043e\u0433\u043b\u0430\u0448\u0435\u043d\u0438\u044f")
            for line in doc_module.intro_conventions.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        lines.append("## 2. \u0421\u043e\u0434\u0435\u0440\u0436\u0430\u043d\u0438\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430")
        if doc_module.content_purpose:
            lines.append("### 2.1 \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435")
            lines.append(doc_module.content_purpose.strip())
            lines.append("")
        if doc_module.content_materials:
            lines.append("### 2.2 \u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b")
            for line in doc_module.content_materials.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")
        if doc_module.content_preparation:
            lines.append("### 2.3 \u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043a\u0430 \u043a \u0440\u0430\u0431\u043e\u0442\u0435")
            for i, line in enumerate(
                [ln for ln in doc_module.content_preparation.splitlines() if ln.strip()], 1
            ):
                lines.append("%d. %s" % (i, line.strip()))
            lines.append("")

        lines.append("## 3. \u0421\u043f\u0438\u0441\u043e\u043a \u0444\u0443\u043d\u043a\u0446\u0438\u0439")
        lines.append("")
        for func in doc_module.function_ids:
            lines.append("### \u0424\u0443\u043d\u043a\u0446\u0438\u044f %d: %s" % (func.number or 0, func.name or ""))
            lines.append("")
            if func.description:
                lines.append("**\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435:** %s" % func.description.strip())
                lines.append("")
            if func.requirements:
                lines.append("**\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:** %s" % func.requirements.strip())
                lines.append("")
            # Safe step_lines: use method if exists, else fall back to field
            if hasattr(func, 'step_lines') and callable(func.step_lines):
                try:
                    steps = func.step_lines()
                except Exception:  # noqa: BLE001
                    steps = [
                        ln.strip()
                        for ln in (func.steps or "").splitlines()
                        if ln.strip()
                    ]
            else:
                steps = [
                    ln.strip()
                    for ln in (func.steps or "").splitlines()
                    if ln.strip()
                ]
            if steps:
                lines.append("**\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f:**")
                for i, step in enumerate(steps, 1):
                    lines.append("%d. %s" % (i, step))
                lines.append("")
            lines.append("> %s" % (_SCREENSHOT_PLACEHOLDER % (func.name or "")))
            lines.append("")
            if func.result:
                lines.append("**\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442:** %s" % func.result.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

        if doc_module.model_ids:
            lines.append("## \u041f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435. \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043f\u043e\u043b\u0435\u0439")
            lines.append("")
            for model in doc_module.model_ids:
                lines.append(
                    "### %s \u2014 %s" % (
                        model.display_name or model.technical_name,
                        "`%s`" % model.technical_name,
                    )
                )
                if model.description:
                    lines.append(model.description.strip())
                    lines.append("")
                rows = model.field_table_json or []
                if rows:
                    lines.append("| \u041f\u043e\u043b\u0435 | \u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 | \u0422\u0438\u043f | \u041e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0435 | \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 |")
                    lines.append("|------|-------|-----|-----------|---------|")
                    for r in rows:
                        lines.append("| `%s` | %s | %s | %s | %s |" % (
                            r.get("name", ""),
                            r.get("label", ""),
                            r.get("type", ""),
                            "\u0414\u0430" if r.get("required") else "",
                            (r.get("help") or "").replace("\n", " ").replace("|", "\\|"),
                        ))
                    lines.append("")

        if doc_module.bibliography:
            lines.append("## 4. \u041b\u0438\u0442\u0435\u0440\u0430\u0442\u0443\u0440\u0430")
            for line in doc_module.bibliography.splitlines():
                if line.strip():
                    lines.append("- %s" % line.strip())
            lines.append("")

        if doc_module.glossary:
            lines.append("## 5. \u0421\u043b\u043e\u0432\u0430\u0440\u044c \u0442\u0435\u0440\u043c\u0438\u043d\u043e\u0432")
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
                "title": _("\u0421\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b"),
                "message": _(
                    "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0437\u0430\u0445\u0432\u0430\u0442 \u043e\u0442\u043a\u043b\u044e\u0447\u0451\u043d. \u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u0435 \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b \u0432\u0440\u0443\u0447\u043d\u0443\u044e: "
                    "\u043e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0437\u0430\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 \u043c\u043e\u0434\u0443\u043b\u044c \u2192 \u0432\u043a\u043b\u0430\u0434\u043a\u0430 \u00ab\u0424\u0443\u043d\u043a\u0446\u0438\u0438\u00bb \u2192 "
                    "\u043e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0444\u0443\u043d\u043a\u0446\u0438\u044e \u2192 \u043f\u043e\u043b\u0435 \u00ab\u0421\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u00bb."
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
            raise UserError(_("\u041d\u0435\u0447\u0435\u0433\u043e \u044d\u043a\u0441\u043f\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c. \u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u0441\u0431\u043e\u0440 \u0442\u0435\u043a\u0441\u0442\u043e\u0432."))
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
                    "Generation %s has %s screenshot task(s) awaiting manual upload.",
                    gen.id, remaining,
                )
        return True
