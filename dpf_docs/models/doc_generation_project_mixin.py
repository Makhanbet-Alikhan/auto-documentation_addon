# -*- coding: utf-8 -*-
"""
Mixin that adds snapshot-based project task import to doc.generation.

New behaviour added to doc.generation via _inherit
--------------------------------------------------
1.  action_collect() now imports snapshots BEFORE running per-module enrichment,
    so the enricher always reads from snapshots rather than live project.task.

2.  action_enrich_from_tasks() auto-imports snapshots if none exist yet,
    so the user does not need to click two separate buttons.

3.  action_reimport_project_tasks() — a dedicated button that (re)downloads all
    project tasks into snapshots without running full generation.  Safe to call
    multiple times; always wipes and re-imports.

4.  project_snapshot_count — computed informational counter shown on the form.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocGenerationProjectMixin(models.Model):
    """Extends doc.generation with project task snapshot import capabilities."""

    _inherit = 'doc.generation'

    project_snapshot_count = fields.Integer(
        string='Imported Task Snapshots',
        compute='_compute_snapshot_count',
        store=False,
    )

    def _compute_snapshot_count(self):
        Snapshot = self.env['doc.project.task.snapshot']
        for rec in self:
            rec.project_snapshot_count = Snapshot.search_count(
                [('generation_id', '=', rec.id)]
            )

    # ------------------------------------------------------------------
    # Public button actions
    # ------------------------------------------------------------------

    def action_reimport_project_tasks(self):
        """
        Button: (re)import all tasks from the selected project into snapshots.

        Deletes previous snapshots for this generation first, then re-imports.
        Safe to call multiple times.
        """
        self.ensure_one()
        if not self.project_task_project_id:
            raise UserError(_(
                'No project selected. Set the "Project for enrichment" field first.'
            ))
        stats = self._do_import_project_tasks()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Project Tasks Imported'),
                'message': _('%s task snapshots imported from project.') % stats.get('imported', 0),
                'type': 'success',
                'sticky': False,
            },
        }

    def _do_import_project_tasks(self):
        """Internal: call snapshot import and return stats dict."""
        self.ensure_one()
        Snapshot = self.env['doc.project.task.snapshot']
        _logger.info(
            '_do_import_project_tasks: generation_id=%s project_id=%s',
            self.id, self.project_task_project_id,
        )
        stats = Snapshot.import_from_project(
            self.id, self.project_task_project_id
        )
        _logger.info('_do_import_project_tasks: stats=%s', stats)
        return stats

    def _ensure_snapshots_loaded(self):
        """
        Make sure snapshots are populated for this generation.

        If none exist yet and a project_id is configured, imports them
        automatically.  Idempotent when snapshots already exist.
        """
        self.ensure_one()
        Snapshot = self.env['doc.project.task.snapshot']
        existing = Snapshot.search_count([('generation_id', '=', self.id)])
        if existing == 0 and self.project_task_project_id:
            _logger.info(
                '_ensure_snapshots_loaded: no snapshots for gen=%s — '
                'importing now from project_id=%s',
                self.id, self.project_task_project_id,
            )
            self._do_import_project_tasks()

    # ------------------------------------------------------------------
    # Override action_collect
    # ------------------------------------------------------------------

    def action_collect(self):
        """
        Override: import project task snapshots BEFORE collecting module texts.

        Ensures that enrichment during collection already has snapshot data
        available, even on the very first run.
        """
        self.ensure_one()

        if self.enrich_from_project and self.project_task_project_id:
            _logger.info(
                'action_collect: pre-importing project tasks for generation_id=%s',
                self.id,
            )
            self._do_import_project_tasks()
        elif self.enrich_from_project and not self.project_task_project_id:
            _logger.info(
                'action_collect: enrich_from_project=True but no project selected '
                '— skipping snapshot import'
            )

        return super().action_collect()

    # ------------------------------------------------------------------
    # Override action_enrich_from_tasks
    # ------------------------------------------------------------------

    def action_enrich_from_tasks(self):
        """
        Override: ensure snapshots are populated before running enrichment.

        If the user clicks 'Enrich from Tasks' without having imported
        snapshots first, they are auto-imported here.
        """
        self.ensure_one()
        if not self.doc_module_ids:
            raise UserError(_('Run "1. Collect Texts" first.'))
        if not self.project_task_project_id:
            raise UserError(_(
                'No project selected. Set the "Project for enrichment" field and retry.'
            ))

        self._ensure_snapshots_loaded()
        return super().action_enrich_from_tasks()
