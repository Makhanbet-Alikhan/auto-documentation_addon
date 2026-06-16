# -*- coding: utf-8 -*-
"""Generation orchestrator."""
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
        string="Name", required=True, default=lambda self: _("New Run")
    )
    module_ids = fields.Many2many(
        "ir.module.module",
        string="Installed Modules",
        domain="[('state', '=', 'installed')]",
    )
    module_names = fields.Char(
        string="Additional Modules",
        help="Optional. Comma-separated technical names.",
    )

    # Soft dependency on project.project \u2014 no hard FK.
    project_task_project_id = fields.Integer(
        string="Project ID",
        default=0,
        help="Internal: stores project.project id without a hard FK.",
    )
    project_task_project_name = fields.Char(
        string="Project Name",
        help="Name of the selected project (resolved to project_task_project_id).",
    )

    # Global snapshot set (preferred over per-generation import)
    snapshot_set_id = fields.Many2one(
        'doc.project.snapshot.set',
        string='Global Snapshot Set',
        ondelete='set null',
        help=(
            'Select a pre-downloaded snapshot set (Tools > Documentation > '
            'Project Snapshots). When set, enrichment reads from the global '
            'set instead of downloading tasks again for each run.'
        ),
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
    use_llm_caption = fields.Boolean(string="Use LLM Captions")
    enrich_from_project = fields.Boolean(
        string="Enrich from Project Tasks",
        default=True,
    )

    # ------------------------------------------------------------------
    # Project picker wizard button
    # ------------------------------------------------------------------
    def action_pick_project(self):
        """Open the project picker wizard."""
        self.ensure_one()
        if 'project.project' not in self.env:
            raise UserError(_("The 'project' module is not installed."))
        wizard = self.env['doc.project.picker.wizard'].create({
            'generation_id': self.id,
            'project_name': self.project_task_project_name or '',
        })
        return {
            'name': _('Select Project'),
            'type': 'ir.actions.act_window',
            'res_model': 'doc.project.picker.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ------------------------------------------------------------------
    # Onchange: auto-fill run name from selected module
    # ------------------------------------------------------------------
    @api.onchange('module_ids')
    def _onchange_module_ids(self):
        default_name = _("New Run")
        current_name = (self.name or '').strip()
        if current_name and current_name != default_name:
            return
        if not self.module_ids:
            return
        first_module = self.module_ids[:1]
        auto_name = first_module.shortdesc or first_module.name or ''
        if auto_name:
            self.name = auto_name

    # ------------------------------------------------------------------
    # Step 1: collect texts
    # ------------------------------------------------------------------
    def _resolve_module_names(self):
        names = list(self.module_ids.mapped("name"))
        for raw in (self.module_names or "").split(","):
            raw = raw.strip()
            if raw and raw not in names:
                names.append(raw)
        return names

    def action_collect(self):
        self.ensure_one()
        modules = self._resolve_module_names()
        if not modules:
            raise UserError(_("Select at least one module to document."))

        # Pre-import per-generation snapshots if no global set is selected
        if self.enrich_from_project and not self.snapshot_set_id:
            if self.project_task_project_id:
                _logger.info(
                    'action_collect: no snapshot_set \u2014 importing per-gen snaps '
                    'for project_id=%s', self.project_task_project_id
                )
                self.env['doc.project.task.snapshot'].import_from_project(
                    self.id, self.project_task_project_id
                )
            else:
                _logger.info(
                    'action_collect: enrich_from_project=True but no project '
                    'and no snapshot_set \u2014 skipping snapshot import'
                )

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
        capturer.capture_all(self)
        return True

    def action_print_report(self):
        self.ensure_one()
        return self.env.ref('dpf_docs.action_report_doc_generation').report_action(self)

    def action_download_word(self):
        self.ensure_one()
        exporter = self.env['doc.word.export']
        return exporter.export_generation(self)
