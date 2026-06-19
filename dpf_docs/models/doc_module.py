# -*- coding: utf-8 -*-
"""Stored documentation for one module (the aggregate result)."""
import base64
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class DocModule(models.Model):
    _name = "doc.module"
    _description = "Auto Doc - Documented Module"
    _order = "create_date desc"

    name = fields.Char(string="Title", required=True)
    generation_id = fields.Many2one(
        "doc.generation", string="Generation Run", ondelete="cascade"
    )
    technical_name = fields.Char(
        string="Module", required=True, help="Technical module name."
    )
    description = fields.Text(string="Module Description")

    # Primary model detected by introspector (most FK-referenced model)
    primary_model = fields.Char(
        string="Primary Model",
        help="Technical name of the main model of this module (auto-detected).",
    )

    # --- User-manual metadata ---
    system_name = fields.Char(string="System Name")
    manual_version = fields.Char(string="Manual Version", default="1.0")
    developer = fields.Char(string="Developer")
    city_year = fields.Char(string="City / Year")
    platform_version = fields.Char(string="Platform Version", default="Odoo 19")
    intro_user_categories = fields.Text(string="1.1 User Categories")
    intro_scope = fields.Text(string="1.2 Scope")
    intro_purpose = fields.Text(string="1.3 Document Purpose")
    intro_conventions = fields.Text(string="1.4 Conventions")
    content_purpose = fields.Text(string="2.1 Purpose")
    content_materials = fields.Text(string="2.2 Materials")
    content_preparation = fields.Text(string="2.3 Preparation")
    bibliography = fields.Text(string="8 Bibliography")
    glossary = fields.Text(string="9 Glossary")

    menu_ids = fields.One2many("doc.menu", "doc_module_id", string="Menus")
    model_ids = fields.One2many("doc.model.info", "doc_module_id", string="Models")
    function_ids = fields.One2many("doc.function", "doc_module_id", string="Functions")

    # ------------------------------------------------------------------
    # Extended documentation sections (populated manually or via future
    # automated introspectors).  All are optional — the Word exporter
    # skips a section entirely when the corresponding relation is empty.
    # ------------------------------------------------------------------
    workflow_state_ids = fields.One2many(
        "doc.workflow.state", "doc_module_id",
        string="3. Lifecycle States",
        help="States and transitions of the main object (workflow/state machine). "
             "When filled, Section 3 appears in the generated Word document.",
    )
    inherited_model_ids = fields.One2many(
        "doc.inherited.model", "doc_module_id",
        string="4. Inherited Model Extensions",
        help="Base Odoo models extended via _inherit. Fields added to those models "
             "are missed by the standard introspector. "
             "When filled, Section 4 appears in the generated Word document.",
    )
    integration_ids = fields.One2many(
        "doc.integration", "doc_module_id",
        string="5. External Integrations",
        help="External services used by the module (MinIO, RabbitMQ, SMTP, etc.). "
             "When filled, Section 5 appears in the generated Word document.",
    )
    analytic_field_ids = fields.One2many(
        "doc.analytic.field", "doc_module_id",
        string="7. Analytic Fields",
        help="Computed KPI fields not visible in the standard field list. "
             "When filled, Section 7 appears in the generated Word document.",
    )
    export_action_ids = fields.One2many(
        "doc.export.action", "doc_module_id",
        string="7. Export Actions",
        help="PDF/XLSX/CSV export buttons on forms or list views. "
             "When filled, Section 7 appears in the generated Word document.",
    )

    menu_count = fields.Integer(string="Menus", compute="_compute_counts", store=True)
    model_count = fields.Integer(string="Models", compute="_compute_counts", store=True)
    captured_count = fields.Integer(string="Screenshots", compute="_compute_counts", store=True)
    function_count = fields.Integer(string="Functions", compute="_compute_counts", store=True)

    markdown = fields.Text(string="Markdown Output")
    pdf_attachment_id = fields.Many2one("ir.attachment", string="PDF File")
    word_attachment_id = fields.Many2one("ir.attachment", string="Word File")

    @api.depends("menu_ids", "model_ids", "function_ids", "menu_ids.capture_state")
    def _compute_counts(self):
        for rec in self:
            rec.menu_count = len(rec.menu_ids)
            rec.model_count = len(rec.model_ids)
            rec.function_count = len(rec.function_ids)
            rec.captured_count = len(
                rec.menu_ids.filtered(lambda m: m.capture_state == "captured")
            )

    # ------------------------------------------------------------------
    # Manual content helpers
    # ------------------------------------------------------------------
    def apply_manual_defaults(self):
        self.ensure_one()
        composer = self.env["doc.text.defaults"]
        defaults = composer.manual_defaults(self)
        values = {field: value for field, value in defaults.items() if not self[field]}
        if values:
            self.write(values)
        return True

    @staticmethod
    def _menu_has_form(menu):
        view_modes = [
            v.strip() for v in (menu.view_modes or "").split(",") if v.strip()
        ]
        if not view_modes:
            return bool(menu.res_model)
        return "form" in view_modes

    def build_functions_from_menus(self):
        self.ensure_one()
        self.function_ids.unlink()
        composer = self.env["doc.text.defaults"]
        number = 0
        menus = self.menu_ids.sorted(
            key=lambda m: ((m.sequence or 999999), (m.complete_name or ""), m.id)
        )
        seen = set()
        for menu in menus:
            if menu.capture_state == "skipped" and not menu.res_model:
                continue
            normalized_views = ",".join(sorted(set(
                v.strip() for v in (menu.view_modes or "").split(",") if v.strip()
            )))
            dedupe_key = (
                (menu.name or "").strip().lower(),
                (menu.res_model or "").strip().lower(),
                normalized_views,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            number += 1
            entry = composer.function_for_menu(menu, number)
            entry.update({
                "doc_module_id": self.id,
                "doc_menu_id": menu.id,
                "sequence": number * 10,
                "number": number,
                "screenshot": menu.screenshot or False,
                "screenshot_source": "menu" if menu.screenshot else "none",
            })
            self.env["doc.function"].create(entry)

            if self._menu_has_form(menu):
                number += 1
                create_entry = composer.function_for_create(menu, number)
                create_entry.update({
                    "doc_module_id": self.id,
                    "doc_menu_id": menu.id,
                    "sequence": number * 10,
                    "number": number,
                    "screenshot": menu.screenshot or False,
                    "screenshot_source": "menu" if menu.screenshot else "none",
                })
                self.env["doc.function"].create(create_entry)
        return True

    def capture_screenshots(self, only_missing=True):
        self.ensure_one()
        result = self.env["doc.screenshot.capturer"].capture_module(
            self, only_missing=only_missing
        )
        self.refresh_function_screenshots()
        return result

    def action_capture_screenshots(self):
        self.ensure_one()
        result = self.capture_screenshots(only_missing=True)
        message = _("Screenshots captured: %(captured)s, failed: %(failed)s.") % result
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Automatic Screenshots"),
                "message": message,
                "type": "success" if not result.get("failed") else "warning",
                "sticky": False,
            },
        }

    def refresh_function_screenshots(self):
        self.ensure_one()
        for func in self.function_ids.filtered(lambda f: f.doc_menu_id):
            if func.screenshot_source == "manual":
                continue
            menu = func.doc_menu_id
            if menu.capture_state == "captured" and menu.screenshot:
                func.screenshot = menu.screenshot
                func.screenshot_source = "menu"
        return True

    def pending_screenshot_tasks(self):
        self.ensure_one()
        return [
            menu.to_task_dict()
            for menu in self.menu_ids.filtered(
                lambda m: m.web_url and m.capture_state in ("pending", "error")
            )
        ]

    def action_render_markdown(self):
        """Recompute Markdown."""
        self.ensure_one()
        try:
            md = self.env["doc.generation"]._render_markdown(self)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "action_render_markdown: error for module %s: %s",
                self.technical_name, exc, exc_info=True,
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Error"),
                    "message": str(exc),
                    "type": "danger",
                    "sticky": True,
                },
            }
        self.with_context(no_recompute=True).write({"markdown": md})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Markdown"),
                "message": _("Маркдаун сгенерирован."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_enrich_from_snapshot(self):
        """Button: enrich this module from the project snapshot."""
        self.ensure_one()
        generation = self.generation_id
        if not generation or not generation.snapshot_set_id:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Project Snapshot"),
                    "message": _(
                        "В генерации не указан Snapshot Set. "
                        "Откройте запись генерации и выберите Project Snapshot Set."
                    ),
                    "type": "warning",
                    "sticky": True,
                },
            }
        stats = self.env["doc.project.enricher"].enrich_module(self, overwrite=False)
        if stats["reason"] == "no_matching_tasks":
            msg = _("Задач с тегом [%s] не найдено.") % self.technical_name
            notif_type = "warning"
        elif stats["reason"] == "enriched":
            msg = _(
                "Обогащено: функций %s, меню %s."
            ) % (stats["functions_enriched"], stats["menus_enriched"])
            notif_type = "success"
        else:
            msg = _("reason: %s") % stats["reason"]
            notif_type = "info"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Project Snapshot Enrichment"),
                "message": msg,
                "type": notif_type,
                "sticky": False,
            },
        }

    def action_print_pdf_manual(self):
        self.ensure_one()
        self._auto_capture_if_enabled()
        self.refresh_function_screenshots()
        return self.env.ref("dpf_docs.action_report_doc_module").report_action(self)

    def _auto_capture_if_enabled(self):
        self.ensure_one()
        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "dpf_docs.auto_capture", "1"
        )
        capturer = self.env["doc.screenshot.capturer"]
        if enabled not in ("1", "true", "True") or not capturer.is_available():
            return
        try:
            capturer.capture_module(self, only_missing=True)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Auto-capture failed for module %s.",
                self.technical_name, exc_info=True,
            )

    def _build_word_attachment(self):
        self.ensure_one()
        self._auto_capture_if_enabled()
        self.refresh_function_screenshots()
        data = self.env["doc.word.export"].build_docx(self)
        filename = "%s_module_documentation.docx" % (self.technical_name or "module")
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(data),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document",
        })
        self.word_attachment_id = attachment.id
        return attachment

    def action_download_word(self):
        self.ensure_one()
        attachment = self._build_word_attachment()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }
