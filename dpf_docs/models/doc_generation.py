# -*- coding: utf-8 -*-
"""
doc_generation.py — core generation orchestrator.

Responsible for:
  1. Collecting texts (menus, models, functions) from installed modules.
  2. Running optional enrichment from project task snapshots.
  3. Triggering screenshot capture.
  4. Exporting the final Word document.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import text_composer

_logger = logging.getLogger(__name__)


class DocGeneration(models.Model):
    _name = "doc.generation"
    _description = "Auto Doc - Documentation Generation Run"
    _order = "id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Name", required=True, default="New Documentation")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("awaiting_shots", "Awaiting Screenshots"),
            ("done", "Done"),
        ],
        string="State",
        default="draft",
        tracking=True,
    )

    module_names = fields.Char(
        string="Modules",
        help="Comma-separated technical module names, e.g. dpf_events,dpf_portal",
    )
    doc_module_ids = fields.One2many(
        "doc.module", "generation_id", string="Documented Modules"
    )

    # ---------- project enrichment ----------
    enrich_from_project = fields.Boolean(
        string="Enrich from Project Tasks",
        default=False,
        help="If enabled, menu/function descriptions are enriched from project task snapshots.",
    )
    project_task_project_id = fields.Integer(
        string="Project ID",
        default=0,
        help="ID of the project.project record to import tasks from.",
    )
    snapshot_set_id = fields.Many2one(
        "doc.project.snapshot.set",
        string="Global Snapshot Set",
        help="Reusable pre-imported snapshot set shared across generation runs.",
        ondelete="set null",
    )

    # ---------- word export ----------
    word_attachment_id = fields.Many2one(
        "ir.attachment", string="Word Document", readonly=True
    )

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_collect(self):
        self.ensure_one()
        if not self.module_names:
            raise UserError(_("Please enter at least one module name."))

        modules = [m.strip() for m in self.module_names.split(",") if m.strip()]
        introspector = self.env["doc.introspector"]
        parser = self.env["doc.source.parser"]

        self.doc_module_ids.unlink()
        for module_name in modules:
            self._collect_one_module(module_name, introspector, parser)

        self.state = "awaiting_shots"
        return True

    def _collect_one_module(self, module_name, introspector, parser):
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

        doc_module.apply_manual_defaults()
        doc_module.build_functions_from_menus()

        # Run enrichment after menus/functions are built
        has_snaps = (
            (self.snapshot_set_id and self.snapshot_set_id.id)
            or self.project_task_project_id
        )
        if self.enrich_from_project and has_snaps:
            self._run_enrichment(doc_module, overwrite=False)

        return doc_module

    def _run_enrichment(self, doc_module, overwrite=False):
        try:
            enricher = self.env['doc.project.enricher']
            _logger.info(
                '_run_enrichment: module=%s overwrite=%s snapshot_set=%s project_id=%s',
                doc_module.technical_name, overwrite,
                self.snapshot_set_id.id if self.snapshot_set_id else None,
                self.project_task_project_id,
            )
            stats = enricher.enrich_module(
                doc_module,
                overwrite=overwrite,
                project_id=self.project_task_project_id or False,
            )
            _logger.info('_run_enrichment: done stats=%s', stats)
        except Exception:
            _logger.warning(
                '_run_enrichment: failed for %s (non-fatal)',
                doc_module.technical_name, exc_info=True,
            )

    def action_enrich_from_tasks(self):
        self.ensure_one()
        if not self.doc_module_ids:
            raise UserError(_("Run '1. Collect Texts' first."))

        has_source = (
            (self.snapshot_set_id and self.snapshot_set_id.id)
            or self.project_task_project_id
        )
        if not has_source:
            raise UserError(_(
                'No snapshot source configured. Either:\n'
                '  \u2022 Select a Global Snapshot Set, or\n'
                '  \u2022 Enter a project name and click "Re-import from Project".'
            ))

        # Auto-import per-gen snaps if no global set and no existing per-gen snaps
        if not self.snapshot_set_id and self.project_task_project_id:
            existing = self.env['doc.project.task.snapshot'].search_count(
                [('generation_id', '=', self.id)]
            )
            if existing == 0:
                _logger.info(
                    'action_enrich_from_tasks: no per-gen snaps \u2014 auto-importing'
                )
                self.env['doc.project.task.snapshot'].import_from_project(
                    self.id, self.project_task_project_id
                )

        total = {'menus_enriched': 0, 'functions_enriched': 0}
        for doc_module in self.doc_module_ids:
            try:
                enricher = self.env['doc.project.enricher']
                stats = enricher.enrich_module(
                    doc_module,
                    overwrite=True,
                    project_id=self.project_task_project_id or False,
                )
                total['menus_enriched'] += stats.get('menus_enriched', 0)
                total['functions_enriched'] += stats.get('functions_enriched', 0)
                _logger.info(
                    'action_enrich_from_tasks: module=%s stats=%s',
                    doc_module.technical_name, stats,
                )
            except Exception:
                _logger.warning(
                    'action_enrich_from_tasks: failed for %s',
                    doc_module.technical_name, exc_info=True,
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Enrichment complete'),
                'message': _(
                    'Menus: %s, Functions: %s enriched from task snapshots.'
                ) % (total['menus_enriched'], total['functions_enriched']),
                'type': 'success',
                'sticky': False,
            },
        }

    def _build_menus(self, doc_module, module_name, introspector):
        nodes = introspector.get_menu_tree(module_name)
        for node in nodes:
            res_model = node.get("res_model")
            fields_meta = introspector.get_fields_meta(res_model) if res_model else {}
            caption = text_composer.compose_menu_caption(
                node["name"], res_model, node.get("view_modes"), fields_meta
            )
            key_fields = self._build_key_fields(fields_meta)
            self.env["doc.menu"].create({
                "doc_module_id": doc_module.id,
                "menu_xmlid": node.get("complete_name"),
                "name": node["name"],
                "complete_name": node.get("complete_name"),
                "sequence": node.get("sequence", 10),
                "odoo_menu_id": node.get("menu_id"),
                "action_id": node.get("action_id"),
                "res_model": res_model,
                "view_modes": node.get("view_modes"),
                "web_url": node.get("web_url"),
                "caption": caption,
                "key_fields": key_fields,
            })

    def _build_key_fields(self, fields_meta):
        key_list = []
        for fname, fmeta in (fields_meta or {}).items():
            if fmeta.get("required") or fmeta.get("store") is False:
                continue
            label = fmeta.get("string") or fname
            help_text = fmeta.get("help") or ""
            entry = label
            if help_text:
                entry = f"{label} \u2014 {help_text[:80]}"
            key_list.append(entry)
            if len(key_list) >= 10:
                break
        return "\n".join(key_list) if key_list else ""

    def _build_models(self, doc_module, module_name, introspector, parser, parsed):
        """
        Build doc.model.info records for all models defined by the module.

        Uses introspector.get_module_models() which returns a list of dicts:
            [{'model': 'dpf.event', 'name': 'Event', 'transient': False}, ...]
        Then calls get_fields_meta() per model for field-level detail.
        """
        # FIX: was incorrectly calling get_models_meta() — method is get_module_models()
        module_models = introspector.get_module_models(module_name)
        source_models = parsed.get("models", {})
        for entry in (module_models or []):
            model_name = entry.get("model", "")
            if not model_name:
                continue
            fields_meta = introspector.get_fields_meta(model_name)
            doc_str = source_models.get(model_name, {}).get("docstring", "")
            description = text_composer.compose_model_description(
                entry.get("name", model_name), fields_meta, doc_str
            )
            self.env["doc.model.info"].create({
                "doc_module_id": doc_module.id,
                "name": entry.get("name", model_name),
                "model": model_name,
                "description": description,
            })

    def action_capture_screenshots(self):
        self.ensure_one()
        capturer = self.env['doc.screenshot.capturer']
        capturer.run_for_generation(self)
        return True

    def action_export_word(self):
        self.ensure_one()
        exporter = self.env['doc.word.export']
        attachment = exporter.export_generation(self)
        self.word_attachment_id = attachment
        self.state = 'done'
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }
