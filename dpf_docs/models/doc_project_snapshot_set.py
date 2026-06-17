# -*- coding: utf-8 -*-
"""
Global project task snapshot set.

Design goals
------------
* ONE snapshot set = ONE project download.
* The set is completely independent of doc.generation — you can delete the
  Projects module and the snapshots survive (all task data is copied).
* A single set can be reused across any number of documentation runs.
* Import can be re-run at any time to refresh data from Projects.
  If Projects module is absent, the old snapshots are kept as-is.

Workflow
--------
1. Go to  Documentation > Project Snapshots  (global menu).
2. Create a new snapshot set, enter the Project ID (e.g. id from the URL
   when you open your project in Odoo Projects) and a human name.
3. Click  "↓ Import / Refresh from Project".
   - Downloads ALL tasks (main tasks + ALL subtasks recursively).
   - Stores a full plain-text copy — HTML is stripped at import time.
   - The original project.task records are NOT referenced (no hard FK).
4. On the doc.generation form, optionally select this snapshot set.
5. During enrichment the enricher reads from the snapshot set and matches
   tasks to the documented module by its technical name tag (e.g. [dpf_docs]).
   Matching is optional — if no tasks are tagged for this module nothing happens.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocProjectSnapshotSet(models.Model):
    """One global snapshot of a project — fully self-contained, survives project deletion."""

    _name = 'doc.project.snapshot.set'
    _description = 'Auto Doc - Global Project Snapshot Set'
    _order = 'project_name, id'

    # ------------------------------------------------------------------ #
    # Fields                                                               #
    # ------------------------------------------------------------------ #

    name = fields.Char(
        string='Name',
        required=True,
        help='Human label, e.g. "ЦПФ Этап 2 — June 2026".',
    )
    project_id = fields.Integer(
        string='Project ID',
        index=True,
        help=(
            'ID of the project.project record to import from. '
            'Find it in the URL when you open the project in Odoo Projects. '
            'Once imported, this value is only used for the "Refresh" action — '
            'all task data is already stored in the snapshot records.'
        ),
    )
    project_name = fields.Char(
        string='Project Name (cached)',
        help='Name of the project at the last import. Kept as a reference after deletion.',
    )
    last_import_date = fields.Datetime(
        string='Last Imported',
        readonly=True,
    )
    task_count = fields.Integer(
        string='Total Tasks',
        compute='_compute_counts',
        store=False,
    )
    root_task_count = fields.Integer(
        string='Root Tasks',
        compute='_compute_counts',
        store=False,
    )
    subtask_count = fields.Integer(
        string='Subtasks',
        compute='_compute_counts',
        store=False,
    )
    snapshot_ids = fields.One2many(
        'doc.project.task.snapshot',
        'snapshot_set_id',
        string='Task Snapshots',
    )
    notes = fields.Text(
        string='Notes',
        help='Any free-form notes about this snapshot (source, date, branch, etc.).',
    )

    # ------------------------------------------------------------------ #
    # Computed                                                             #
    # ------------------------------------------------------------------ #

    def _compute_counts(self):
        Snap = self.env['doc.project.task.snapshot']
        for rec in self:
            all_snaps = Snap.search([('snapshot_set_id', '=', rec.id)])
            rec.task_count = len(all_snaps)
            roots = all_snaps.filtered(lambda s: s.depth == 0)
            rec.root_task_count = len(roots)
            rec.subtask_count = rec.task_count - rec.root_task_count

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def action_import(self):
        """
        Download ALL tasks (main + subtasks) from project_id and store them
        in this snapshot set.

        Safe to call multiple times — existing snapshots are replaced.
        If project.task model is not installed, raises a clear UserError.
        """
        self.ensure_one()
        if not self.project_id:
            raise UserError(_(
                'Please set a Project ID before importing.\n'
                'Open your project in Odoo Projects and copy the numeric id '
                'from the browser URL (e.g. /odoo/project/42).'
            ))

        if 'project.task' not in self.env:
            raise UserError(_(
                'The Odoo Projects module is not installed. '
                'Cannot import tasks.'
            ))

        stats = self.env['doc.project.task.snapshot'].import_into_set(
            snapshot_set_id=self.id,
            project_id=self.project_id,
        )

        # Cache project name — works even if Projects module is gone next time
        if 'project.project' in self.env:
            proj = self.env['project.project'].sudo().browse(self.project_id)
            if proj.exists():
                self.project_name = proj.name

        self.last_import_date = fields.Datetime.now()

        imported = stats.get('imported', 0)
        roots = stats.get('root_tasks', 0)
        subtasks = stats.get('subtasks', 0)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import complete'),
                'message': _(
                    '%s tasks imported (%s root tasks, %s subtasks).'
                ) % (imported, roots, subtasks),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_view_snapshots(self):
        """Open the task snapshot list for this set."""
        self.ensure_one()
        return {
            'name': _('Task Snapshots — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'doc.project.task.snapshot',
            'view_mode': 'list,form',
            'domain': [('snapshot_set_id', '=', self.id)],
            'context': {'default_snapshot_set_id': self.id},
        }

    def action_view_modules(self):
        """
        Show unique module tags found in this snapshot set.
        Useful to verify which modules have tasks tagged [technical_name].
        """
        self.ensure_one()
        tags = self.env['doc.project.task.snapshot'].search([
            ('snapshot_set_id', '=', self.id),
            ('module_tag', '!=', False),
        ]).mapped('module_tag')
        unique_tags = sorted(set(tags))
        msg = _('Module tags found in this snapshot:\n\n%s') % ('\n'.join(
            '• ' + t for t in unique_tags
        ) or _('(none — no tasks have [tag] prefixes)'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Module tags in "%s"') % self.name,
                'message': msg,
                'type': 'info',
                'sticky': True,
            },
        }
