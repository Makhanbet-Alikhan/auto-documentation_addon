# -*- coding: utf-8 -*-
"""Generation orchestrator.

A ``doc.generation`` record is one run: the user picks one or more installed
modules, presses *Generate*, and this model coordinates the whole pipeline:

1. introspect the ORM (menus, actions, fields)         -> doc.introspector
2. parse the source code (docstrings, comments)         -> doc.source.parser
3. compose texts (deterministic, optional LLM)          -> services.text_composer
4. persist doc.module / doc.menu / doc.model.info
5. expose screenshot tasks to the Playwright worker     -> controllers.doc_api
6. render Markdown / PDF once screenshots are uploaded   -> _render_markdown / report

The actual screenshots are taken outside Odoo by the Node worker, because the
Owl 2 web client only renders in a real browser.
"""
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import text_composer

_logger = logging.getLogger(__name__)


class DocGeneration(models.Model):
    _name = "doc.generation"
    _description = "Auto Doc - Generation Run"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(
        string="Name", required=True, default=lambda self: _("New Generation")
    )
    # Preferred way to choose what to document: pick installed modules from a
    # dropdown. The worker reads module code automatically from disk, so no
    # files need to be uploaded -- only a selection is required.
    module_ids = fields.Many2many(
        "ir.module.module",
        string="Installed Modules",
        domain="[('state', '=', 'installed')]",
        help="Select one or more installed modules to document.",
    )
    # Optional fallback: free-text technical names (comma-separated). Useful for
    # scripting / automation. Merged with the selected modules above.
    module_names = fields.Char(
        string="Extra Module Names",
        help="Optional comma-separated technical names, e.g. 'sale,my_addon'. "
             "Merged with the selected installed modules.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("collected", "Texts Collected"),
            ("awaiting_shots", "Awaiting Screenshots"),
            ("done", "Done"),
        ],
        string="State",
        default="draft",
        tracking=True,
    )
    doc_module_ids = fields.One2many(
        "doc.module", "generation_id", string="Documented Modules"
    )
    use_llm_caption = fields.Boolean(
        string="Use LLM Captions",
        help="If set, the worker may caption screenshots with a Vision LLM.",
    )

    # ------------------------------------------------------------------
    # Step 1-4: collect texts + structure
    # ------------------------------------------------------------------
    def _resolve_module_names(self):
        """Return the de-duplicated list of module technical names to document.

        Combines the dropdown selection with any free-text names. This is how
        the addon decides which installed modules to introspect -- it never
        needs the source files to be uploaded; it reads them from disk.
        """
        names = list(self.module_ids.mapped("name"))
        for raw in (self.module_names or "").split(","):
            raw = raw.strip()
            if raw and raw not in names:
                names.append(raw)
        return names

    def action_collect(self):
        """Introspect + parse + compose + persist for every chosen module."""
        self.ensure_one()
        modules = self._resolve_module_names()
        if not modules:
            raise UserError(_("Please select at least one module to document."))

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
            raise UserError(_("Module '%s' is not installed.") % module_name)

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
        # Fill the user-manual metadata with deterministic Russian defaults and
        # derive one editable "function" entry per documented screen, so the
        # PDF/Word export already matches the reference manual layout.
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
                "capture_state": "pending" if has_action else "skipped",
            })

    def _build_models(self, doc_module, module_name, introspector, parser, parsed):
        """Create doc.model.info records with merged field tables."""
        for minfo in introspector.get_module_models(module_name):
            res_model = minfo["model"]
            fields_meta = introspector.get_fields_meta(res_model)
            # The class name is unknown from ir.model, so we match by model name
            # heuristically against parsed docstrings (best effort).
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
        """Best-effort match of a class docstring to a model name."""
        # Convention: model 'my.model' usually maps to class 'MyModel'.
        candidate = "".join(p.capitalize() for p in res_model.replace(".", "_").split("_"))
        classes = (parsed or {}).get("classes", {})
        if candidate in classes:
            return classes[candidate]
        # Fall back to any class whose doc mentions the model name.
        for doc in classes.values():
            if doc and res_model in doc:
                return doc
        return None

    @staticmethod
    def _guess_field_comments(parsed, res_model):
        """Collect field comments whose class likely maps to ``res_model``."""
        candidate = "".join(p.capitalize() for p in res_model.replace(".", "_").split("_"))
        prefix = "%s." % candidate
        out = {}
        for fkey, comment in (parsed or {}).get("field_comments", {}).items():
            if fkey.startswith(prefix):
                out[fkey[len(prefix):]] = comment
        return out

    # ------------------------------------------------------------------
    # Step 6: rendering
    # ------------------------------------------------------------------
    @api.model
    def _render_markdown(self, doc_module):
        """Render one doc.module to a Markdown string."""
        lines = []
        lines.append("# %s\n" % (doc_module.name or doc_module.technical_name))
        lines.append("> Technical name: `%s`\n" % doc_module.technical_name)
        if doc_module.description:
            lines.append(doc_module.description.strip() + "\n")

        lines.append("\n## Menus\n")
        for menu in doc_module.menu_ids:
            indent = "  " if menu.complete_name and "/" in (menu.complete_name or "") else ""
            lines.append("%s- **%s**" % (indent, menu.name))
            if menu.caption:
                lines.append("%s  %s" % (indent, menu.caption))
            if menu.capture_state == "captured":
                fname = menu.screenshot_filename or ("menu_%s.png" % menu.id)
                lines.append("%s  ![%s](img/%s)" % (indent, menu.name, fname))
            lines.append("")

        lines.append("\n## Models\n")
        for model in doc_module.model_ids:
            lines.append("### `%s` — %s\n" % (model.technical_name, model.display_name or ""))
            if model.description:
                lines.append(model.description.strip() + "\n")
            rows = model.field_table_json or []
            if rows:
                lines.append("| Field | Label | Type | Required | Help |")
                lines.append("|-------|-------|------|----------|------|")
                for r in rows:
                    lines.append("| `%s` | %s | %s | %s | %s |" % (
                        r.get("name", ""),
                        r.get("label", ""),
                        r.get("type", ""),
                        "yes" if r.get("required") else "",
                        (r.get("help") or "").replace("\n", " ").replace("|", "\\|"),
                    ))
                lines.append("")
        return "\n".join(lines)

    def action_render_all(self):
        """Render Markdown for every documented module and mark run done."""
        self.ensure_one()
        for doc_module in self.doc_module_ids:
            doc_module.markdown = self._render_markdown(doc_module)
        self.state = "done"
        return True

    def action_capture_screenshots(self):
        """Capture screenshots automatically for every documented module."""
        self.ensure_one()
        captured = failed = 0
        for doc_module in self.doc_module_ids:
            result = doc_module.capture_screenshots(only_missing=True)
            captured += result.get("captured", 0)
            failed += result.get("failed", 0)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Automatic Screenshots"),
                "message": _("Captured: %s, failed: %s.") % (captured, failed),
                "type": "success" if not failed else "warning",
                "sticky": False,
            },
        }

    def action_print_pdf(self):
        """Trigger the QWeb PDF report for the documented modules."""
        self.ensure_one()
        for doc_module in self.doc_module_ids:
            doc_module._auto_capture_if_enabled()
            doc_module.refresh_function_screenshots()
        return self.env.ref("dpf_docs.action_report_doc_module").report_action(
            self.doc_module_ids
        )

    def action_download_word(self):
        """Generate ONE Word document covering every documented module."""
        self.ensure_one()
        if not self.doc_module_ids:
            raise UserError(_("Nothing to export. Collect texts first."))
        for doc_module in self.doc_module_ids:
            doc_module._auto_capture_if_enabled()
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

    # ------------------------------------------------------------------
    # Worker integration helpers
    # ------------------------------------------------------------------
    def get_worker_spec(self):
        """Return the JSON spec the Playwright worker consumes."""
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
        """Cron fallback: log generations still awaiting screenshots.

        In production an external webhook / queue_job should drive the worker.
        This cron only surfaces pending work; it does not start a browser.
        """
        pending = self.search([("state", "=", "awaiting_shots")])
        for gen in pending:
            remaining = sum(len(m.pending_screenshot_tasks()) for m in gen.doc_module_ids)
            if remaining:
                _logger.info(
                    "Generation %s has %s screenshot task(s) awaiting the worker.",
                    gen.id, remaining,
                )
        return True
