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

    # --- User-manual metadata (mirrors the reference manual cover page) ----
    # These drive the title page, the "Введение" and "Содержание" sections.
    system_name = fields.Char(
        string="System Name",
        help='Product name shown on the cover, e.g. Система "Smart OTM".',
    )
    manual_version = fields.Char(string="Manual Version", default="1.0")
    developer = fields.Char(
        string="Developer", help='Shown as "Разработчик: ..." on the cover.'
    )
    city_year = fields.Char(
        string="City / Year",
        help='Footer of the cover page, e.g. "Астана 2025".',
    )
    platform_version = fields.Char(
        string="Platform Version",
        default="Odoo 19",
        help="Used in the 'Область применения' section.",
    )
    # Free-text intro blocks. Sensible Russian defaults are filled on creation
    # so the export already matches the reference manual out of the box.
    intro_user_categories = fields.Text(string="1.1 User Categories")
    intro_scope = fields.Text(string="1.2 Scope")
    intro_purpose = fields.Text(string="1.3 Document Purpose")
    intro_conventions = fields.Text(string="1.4 Conventions")
    content_purpose = fields.Text(string="2.1 Purpose")
    content_materials = fields.Text(string="2.2 Materials")
    content_preparation = fields.Text(string="2.3 Preparation")
    bibliography = fields.Text(string="4 Bibliography")
    glossary = fields.Text(string="5 Glossary")

    menu_ids = fields.One2many("doc.menu", "doc_module_id", string="Menus")
    model_ids = fields.One2many("doc.model.info", "doc_module_id", string="Models")
    function_ids = fields.One2many(
        "doc.function", "doc_module_id", string="Functions"
    )

    menu_count = fields.Integer(
        string="Menus", compute="_compute_counts", store=True
    )
    model_count = fields.Integer(
        string="Models", compute="_compute_counts", store=True
    )
    captured_count = fields.Integer(
        string="Screenshots", compute="_compute_counts", store=True
    )
    function_count = fields.Integer(
        string="Functions", compute="_compute_counts", store=True
    )

    # Rendered artefacts.
    markdown = fields.Text(string="Markdown Output")
    pdf_attachment_id = fields.Many2one("ir.attachment", string="PDF File")
    word_attachment_id = fields.Many2one("ir.attachment", string="Word File")

    @api.depends(
        "menu_ids", "model_ids", "function_ids", "menu_ids.capture_state"
    )
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
        """Fill empty manual-metadata fields with sensible Russian defaults.

        Mirrors the structure of the reference user manual so a freshly
        collected module already exports a complete, well-formed document.
        """
        self.ensure_one()
        composer = self.env["doc.text.defaults"]
        defaults = composer.manual_defaults(self)
        values = {
            field: value
            for field, value in defaults.items()
            if not self[field]
        }
        if values:
            self.write(values)
        return True

    def build_functions_from_menus(self):
        """(Re)create doc.function entries from this module's menu tree.

        One function is generated per documented screen. For menus whose
        res_model is 'news.post' an additional 'Создание новости' function
        is generated automatically with full field-by-field instructions.
        Duplicate menus (same name + model + view_modes) are skipped. Menus
        are sorted by sequence so the function numbering matches the UI order.
        """
        self.ensure_one()
        self.function_ids.unlink()
        composer = self.env["doc.text.defaults"]
        number = 0

        # Sort menus by sequence first, then by name, then by id as tie-breaker
        menus = self.menu_ids.sorted(
            key=lambda m: ((m.sequence or 999999), (m.complete_name or ""), m.id)
        )

        seen = set()
        for menu in menus:
            # Skip pure container menus that do not open any screen.
            if menu.capture_state == "skipped" and not menu.res_model:
                continue

            # Deduplicate by (name, model, normalised view_modes)
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

            # For news.post screens: auto-generate a dedicated creation guide
            if (menu.res_model or "").strip() == "news.post":
                number += 1
                create_entry = composer.function_for_news_create(menu, number)
                create_entry.update({
                    "doc_module_id": self.id,
                    "doc_menu_id": menu.id,
                    "sequence": number * 10,
                    "number": number,
                    # Reuse the same screenshot — shows the form the user fills in
                    "screenshot": menu.screenshot or False,
                    "screenshot_source": "menu" if menu.screenshot else "none",
                })
                self.env["doc.function"].create(create_entry)

        return True

    def capture_screenshots(self, only_missing=True):
        """Automatically capture screenshots for this module's screens.

        Drives a headless browser (via :class:`doc.screenshot.capturer`) to
        snapshot every documented screen and store each image on its matching
        menu, then syncs them onto the functions. Manual uploads are preserved.
        """
        self.ensure_one()
        result = self.env["doc.screenshot.capturer"].capture_module(
            self, only_missing=only_missing
        )
        # Push freshly captured menu screenshots onto their functions so they
        # render directly under each screen's text in the export.
        self.refresh_function_screenshots()
        return result

    def action_capture_screenshots(self):
        """Button: capture screenshots now and report the outcome."""
        self.ensure_one()
        result = self.capture_screenshots(only_missing=True)
        message = _(
            "Screenshots captured: %(captured)s, failed: %(failed)s."
        ) % result
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
        """Copy the latest captured menu screenshots onto their functions.

        Screenshots are produced asynchronously by the worker after functions
        are first generated, so this re-sync is called right before exporting.
        Manually uploaded screenshots are preserved and never overwritten.
        """
        self.ensure_one()
        for func in self.function_ids.filtered(lambda f: f.doc_menu_id):
            # Respect hand-uploaded images: only auto-fill from the menu when
            # the function was not given a manual screenshot.
            if func.screenshot_source == "manual":
                continue
            menu = func.doc_menu_id
            if menu.capture_state == "captured" and menu.screenshot:
                func.screenshot = menu.screenshot
                func.screenshot_source = "menu"
        return True

    def pending_screenshot_tasks(self):
        """Return the list of screenshot tasks still to be captured."""
        self.ensure_one()
        tasks = []
        for menu in self.menu_ids.filtered(
            lambda m: m.web_url and m.capture_state in ("pending", "error")
        ):
            tasks.append(menu.to_task_dict())
        return tasks

    def action_render_markdown(self):
        """Recompute the Markdown artefact from current child records."""
        self.ensure_one()
        self.markdown = self.env["doc.generation"]._render_markdown(self)
        return True

    def action_print_pdf_manual(self):
        """Button: auto-capture missing screenshots, then print the PDF manual.

        Running through a Python method (instead of a raw report action) lets
        us refresh the screenshots first, so the PDF always contains the latest
        image directly under each screen's text.
        """
        self.ensure_one()
        self._auto_capture_if_enabled()
        self.refresh_function_screenshots()
        return self.env.ref("dpf_docs.action_report_doc_module").report_action(self)

    def _auto_capture_if_enabled(self):
        """Capture missing screenshots before an export, if auto-capture is on.

        Controlled by the ``dpf_docs.auto_capture`` config parameter (default
        on). Failures are swallowed so a capture problem never blocks the
        document download -- screens simply export without an image, and any
        manually uploaded screenshots are always preserved.
        """
        self.ensure_one()
        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "dpf_docs.auto_capture", "1"
        )
        capturer = self.env["doc.screenshot.capturer"]
        if enabled not in ("1", "true", "True") or not capturer.is_available():
            return
        try:
            capturer.capture_module(self, only_missing=True)
        except Exception:  # noqa: BLE001 - never block the export on capture
            _logger.warning(
                "Auto-capture failed for module %s; exporting without new shots.",
                self.technical_name, exc_info=True,
            )

    def _build_word_attachment(self):
        """Generate the .docx, store it as an attachment, and return it."""
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
        """Generate the Word document and return a download action."""
        self.ensure_one()
        attachment = self._build_word_attachment()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }
