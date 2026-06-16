# -*- coding: utf-8 -*-
"""
Mixin that adds snapshot-based project task import to doc.generation.

Added to doc.generation via _inherit:
  1. action_reimport_project_tasks() — button to manually re-download tasks
     into per-generation snapshots.
  2. action_enrich_from_tasks() override — ensures snapshots are loaded before
     enrichment runs.
  3. project_snapshot_count — info counter on the form.

NOTE: action_collect() is NOT overridden here. The base class
doc_generation.py already handles snapshot pre-import inside action_collect().
Overriding it here would cause double-import.
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
        Button: (re)import all tasks from the selected project into per-generation
        snapshots.  Deletes previous snapshots for this generation first.
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
        Ensure per-generation snapshots are populated.

        Called before enrichment if no global snapshot_set_id is set.
        If snapshots already exist they are NOT re-imported (idempotent).
        """
        self.ensure_one()
        if self.snapshot_set_id:
            # Global set is configured — no per-gen import needed.
            return
        Snapshot = self.env['doc.project.task.snapshot']
        existing = Snapshot.search_count([('generation_id', '=', self.id)])
        if existing == 0 and self.project_task_project_id:
            _logger.info(
                '_ensure_snapshots_loaded: no per-gen snaps for gen=%s — '
                'importing from project_id=%s',
                self.id, self.project_task_project_id,
            )
            self._do_import_project_tasks()
