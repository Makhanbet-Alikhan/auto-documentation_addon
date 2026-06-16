# -*- coding: utf-8 -*-
"""
Global (generation-independent) project task snapshot set.

Problem with the old design
---------------------------
Snapshots were stored per doc.generation run (foreign key generation_id).
This meant every new documentation run had to re-download all project tasks,
even if nothing changed in the project.

New design
----------
``doc.project.snapshot.set``  — one record per project (project_id is unique).
    Holds the download timestamp and the list of snapshots.

``doc.project.task.snapshot`` — reused model; now has TWO optional parents:
    * snapshot_set_id  (doc.project.snapshot.set)  — global shared copy
    * generation_id    (doc.generation)             — legacy / generation-scoped copy
    Exactly one of these is set on each record.

Workflow
--------
1. User opens the global menu  Tools > Documentation > Project Snapshots.
2. Creates a snapshot set for project "ЦПФ Этап 2".
3. Clicks  "↓ Import / Refresh from Project".
4. On any doc.generation form the user selects this snapshot set instead of
   (or in addition to) entering a project name.
5. The enricher reads from the snapshot set — no re-download needed.

Backward compatibility
-----------------------
All existing per-generation imports still work; the generation_id path is kept
in doc.project.task.snapshot.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocProjectSnapshotSet(models.Model):
    """Global snapshot of a project.project — shared across generation runs."""

    _name = 'doc.project.snapshot.set'
    _description = 'Auto Doc - Global Project Snapshot Set'
    _order = 'project_name, id'

    name = fields.Char(
        string='Name',
        required=True,
        help='Human label, e.g. "ЦПФ Этап 2 — snapshot 2026-06".',
    )
    project_id = fields.Integer(
        string='Project ID',
        required=True,
        index=True,
        help='project.project id. No hard FK so the project can be deleted.',
    )
    project_name = fields.Char(
        string='Project Name',
        help='Cached name of the project at last import.',
    )
    last_import_date = fields.Datetime(
        string='Last Imported',
        readonly=True,
    )
    task_count = fields.Integer(
        string='Tasks',
        compute='_compute_task_count',
        store=False,
    )
    snapshot_ids = fields.One2many(
        'doc.project.task.snapshot',
        'snapshot_set_id',
        string='Task Snapshots',
    )
    notes = fields.Text(string='Notes')

    def _compute_task_count(self):
        Snap = self.env['doc.project.task.snapshot']
        for rec in self:
            rec.task_count = Snap.search_count([('snapshot_set_id', '=', rec.id)])

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_import(self):
        """Import / refresh all tasks from the linked project."""
        self.ensure_one()
        if not self.project_id:
            raise UserError(_('Set a Project ID first.'))
        stats = self.env['doc.project.task.snapshot'].import_into_set(
            snapshot_set_id=self.id,
            project_id=self.project_id,
        )
        # Cache project name
        if 'project.project' in self.env:
            proj = self.env['project.project'].sudo().browse(self.project_id)
            if proj.exists():
                self.project_name = proj.name
        self.last_import_date = fields.Datetime.now()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import complete'),
                'message': _('%s task snapshots imported.') % stats.get('imported', 0),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_view_snapshots(self):
        """Open the tree view of snapshots for this set."""
        self.ensure_one()
        return {
            'name': _('Task Snapshots — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'doc.project.task.snapshot',
            'view_mode': 'list,form',
            'domain': [('snapshot_set_id', '=', self.id)],
            'context': {'default_snapshot_set_id': self.id},
        }
