# -*- coding: utf-8 -*-
"""Generation orchestrator."""
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

    # Soft dependency on project.project — no hard FK.
    project_task_project_id = fields.Integer(
        string="Project ID",
        default=0,
        help="Internal: stores project.project id without a hard FK.",
    )
    project_task_project_name = fields.Char(
        string="Project Name",
        help="Type the project name here, then click 'Find & Save'.",
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

    project_snapshot_count = fields.Integer(
        string="Per-run Snapshots",
        compute="_compute_project_snapshot_count",
        store=False,
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
    # Computed fields
    # ------------------------------------------------------------------
    def _compute_project_snapshot_count(self):
        """Count per-generation task snapshots."""
        Snapshot = self.env['doc.project.task.snapshot']
        for rec in self:
            rec.project_snapshot_count = Snapshot.search_count(
                [('generation_id', '=', rec.id)]
            )

    # ------------------------------------------------------------------
    # Project: find by name typed directly in the form
    # ------------------------------------------------------------------
    def action_find_project(self):
        """Search for a project by the name typed in project_task_project_name.

        Writes project_task_project_id + project_task_project_name on the
        current record immediately (no dialog needed) and shows a notification.
        If multiple projects match, shows the list so the user can be more
        specific.  If exactly one matches, saves it.

        This is the replacement for the picker wizard: editing a field and
        pressing a button on the *same* form record is the only approach in
        Odoo 17-19 that reliably persists data without reload / dialog tricks.
        """
        self.ensure_one()
        if 'project.project' not in self.env:
            raise UserError(_("The 'project' module is not installed."))

        query = (self.project_task_project_name or '').strip()
        if not query:
            raise UserError(_(
                'Type a project name in the "Project Name" field first, '
                'then click Find & Save.'
            ))

        Project = self.env['project.project'].sudo()
        projects = Project.search([('name', 'ilike', query)], order='name asc', limit=20)

        if not projects:
            raise UserError(_(
                'No project found matching "%s".\n'
                'Check the spelling — the search is case-insensitive.'
            ) % query)

        if len(projects) == 1:
            # Exact single match — save directly.
            self.write({
                'project_task_project_id': projects.id,
                'project_task_project_name': projects.name,
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Project saved'),
                    'message': _('Project "%s" (id=%s) linked. '
                                 'Click \u2b07 Re-import to load tasks.'
                                 ) % (projects.name, projects.id),
                    'type': 'success',
                    'sticky': False,
                },
            }

        # Multiple matches — save the first exact-name match if any,
        # otherwise list all options and ask user to be more specific.
        exact = projects.filtered(lambda p: p.name.lower() == query.lower())
        if len(exact) == 1:
            self.write({
                'project_task_project_id': exact.id,
                'project_task_project_name': exact.name,
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Project saved'),
                    'message': _('Project "%s" (id=%s) linked.')
                               % (exact.name, exact.id),
                    'type': 'success',
                    'sticky': False,
                },
            }

        names = '\n'.join('  • %s (id=%s)' % (p.name, p.id) for p in projects)
        raise UserError(_(
            'Multiple projects match "%s":\n%s\n\n'
            'Make the name more specific or copy-paste the exact name.'
        ) % (query, names))

    # ------------------------------------------------------------------
    # Legacy wizard button — kept for backward compat, now just a hint
    # ------------------------------------------------------------------
    def action_pick_project(self):
        """Legacy: redirect user to inline find approach."""
        raise UserError(_(
            'Type the project name in the "Project Name" field '
            'and click "🔍 Find & Save Project" next to it.'
        ))

    def action_reimport_project_tasks(self):
        """Re-download tasks from project.project into per-generation snapshots."""
        self.ensure_one()
        if not self.project_task_project_id:
            raise UserError(_(
                'No project linked yet.\n'
                'Type the project name and click "Find & Save" first.'
            ))
        result = self.env['doc.project.task.snapshot'].import_from_project(
            self.id, self.project_task_project_id
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import complete'),
                'message': _('%s task snapshots imported from "%s".') % (
                    result.get('imported', 0),
                    self.project_task_project_name or str(self.project_task_project_id),
                ),
                'type': 'success',
                'sticky': False,
            },
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

        if self.enrich_from_project and not self.snapshot_set_id:
            if self.project_task_project_id:
                _logger.info(
                    'action_collect: importing per-gen snaps for project_id=%s',
                    self.project_task_project_id
                )
                self.env['doc.project.task.snapshot'].import_from_project(
                    self.id, self.project_task_project_id
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
            stats = enricher.enrich_module(
                doc_module,
                overwrite=overwrite,
                project_id=self.project_task_project_id or False,
            )
            _logger.info('_run_enrichment: module=%s stats=%s', doc_module.technical_name, stats)
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
                '  \u2022 Type a project name, click "Find & Save", '
                'then click "Re-import from Project".'
            ))

        if not self.snapshot_set_id and self.project_task_project_id:
            existing = self.env['doc.project.task.snapshot'].search_count(
                [('generation_id', '=', self.id)]
            )
            if existing == 0:
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
        module_models = introspector.get_module_models(module_name)
        source_models = parsed.get("models", {})
        for entry in (module_models or []):
            model_name = entry.get("model", "")
            if not model_name:
                continue
            fields_meta = introspector.get_fields_meta(model_name)
            doc_str = source_models.get(model_name, {}).get("docstring", "")
            description = text_composer.compose_model_description(
                model_name, fields_meta, doc_str
            )
            self.env["doc.model.info"].create({
                "doc_module_id": doc_module.id,
                "technical_name": model_name,
                "display_name": entry.get("name", model_name),
                "description": description,
                "field_count": len(fields_meta) if fields_meta else 0,
            })

    # ------------------------------------------------------------------
    # Screenshots — graceful degradation when Playwright not installed
    # ------------------------------------------------------------------
    def action_capture_screenshots(self):
        """Capture screenshots for all documented modules in this generation.

        If Playwright is not installed the user sees a friendly notification
        instead of a crash. Screenshots will be added via a separate module.
        """
        self.ensure_one()
        capturer = self.env['doc.screenshot.capturer']

        if not capturer.is_available():
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Screenshots not available'),
                    'message': _(
                        'Automatic screenshots require the Playwright package.\n'
                        'Install with: pip install playwright && '
                        'playwright install --with-deps chromium\n\n'
                        'Screenshot automation will be added via a separate module. '
                        'Until then upload screenshots manually on each function.'
                    ),
                    'type': 'warning',
                    'sticky': True,
                },
            }

        captured_total = 0
        failed_total = 0
        for doc_module in self.doc_module_ids:
            try:
                result = capturer.capture_module(doc_module, only_missing=True)
                captured_total += result.get('captured', 0)
                failed_total += result.get('failed', 0)
            except Exception as exc:
                _logger.warning(
                    'action_capture_screenshots: module=%s error=%s',
                    doc_module.technical_name, exc, exc_info=True,
                )
                failed_total += 1

        msg_type = 'success' if failed_total == 0 else 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Screenshots done'),
                'message': _('Captured: %s  Failed: %s') % (captured_total, failed_total),
                'type': msg_type,
                'sticky': failed_total > 0,
            },
        }

    # ------------------------------------------------------------------
    # Export: Word
    # ------------------------------------------------------------------
    def action_download_word(self):
        self.ensure_one()
        exporter = self.env['doc.word.export']
        return exporter.export_generation(self)

    # ------------------------------------------------------------------
    # Export: PDF via QWeb report
    # ------------------------------------------------------------------
    def action_download_pdf(self):
        """Render the generation as a PDF using the built-in QWeb report engine."""
        self.ensure_one()
        report_xmlid = 'dpf_docs.report_doc_generation_pdf'
        report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report:
            raise UserError(_(
                'PDF report template not found (xml_id: %s).\n'
                'Make sure the report view is defined in dpf_docs/report/*.xml '
                'and the module has been upgraded.'
            ) % report_xmlid)
        return report.report_action(self)
