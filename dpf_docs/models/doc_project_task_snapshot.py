# -*- coding: utf-8 -*-
"""
Persistent snapshot of project.task data.

Each record represents one task imported from project.task.
Can be attached to either:
  * snapshot_set_id  (doc.project.snapshot.set)  — global shared pool
  * generation_id    (doc.generation)             — legacy per-run pool

Exactly one of these is non-null on each record.
"""
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[(?P<tag>[\w]+)\]\s*', flags=re.UNICODE)


def _strip_tag(name):
    """Return (tag, clean_name) from '[dpf_events] Some text'."""
    if not name:
        return '', ''
    m = _TAG_RE.match(name)
    if m:
        return m.group('tag').lower(), name[m.end():].strip()
    return '', name.strip()


def _html_to_plain(html):
    """Strip HTML tags and decode common entities to plain text."""
    if not html:
        return ''
    text = re.sub(r'<[^>]+>', ' ', html)
    for old, new in [
        ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
        ('&nbsp;', ' '), ('&quot;', '"'), ('&#39;', "'"),
    ]:
        text = text.replace(old, new)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class DocProjectTaskSnapshot(models.Model):
    """One imported project.task record, stored independently of the project module."""

    _name = 'doc.project.task.snapshot'
    _description = 'Auto Doc - Project Task Snapshot'
    _order = 'depth, sequence, id'

    # Parent: one of these two is set, the other is False
    snapshot_set_id = fields.Many2one(
        'doc.project.snapshot.set',
        string='Snapshot Set (global)',
        ondelete='cascade',
        index=True,
    )
    generation_id = fields.Many2one(
        'doc.generation',
        string='Generation Run (legacy)',
        ondelete='cascade',
        index=True,
    )

    # Original task identity — for traceability only, NOT a live FK
    original_task_id = fields.Integer(string='Original task.id')
    original_project_id = fields.Integer(string='Original project.id')

    # Task content snapshot
    name = fields.Char(string='Task Name', required=True)
    description_plain = fields.Text(
        string='Description (plain text)',
        help='HTML description stripped to plain text at import time.',
    )

    # Derived fields for fast lookup
    module_tag = fields.Char(
        string='Module Tag',
        index=True,
        help='Lower-cased [tag] extracted from the task name prefix.',
    )
    name_clean = fields.Char(
        string='Clean Name',
        help='Task name with the [tag] prefix removed.',
    )

    # Tree structure
    depth = fields.Integer(string='Depth', default=0)
    sequence = fields.Integer(string='Sequence', default=10)
    parent_snapshot_id = fields.Many2one(
        'doc.project.task.snapshot',
        string='Parent Snapshot',
        ondelete='set null',
    )
    child_snapshot_ids = fields.One2many(
        'doc.project.task.snapshot',
        'parent_snapshot_id',
        string='Child Snapshots',
    )

    # ------------------------------------------------------------------
    # Import helpers
    # ------------------------------------------------------------------

    @api.model
    def import_into_set(self, snapshot_set_id, project_id):
        """
        Import all tasks from project_id into a global snapshot set.

        Deletes previous snapshots for this set first. Safe to call multiple
        times (idempotent refresh).

        Returns dict {'imported': N, 'skipped': N}.
        """
        return self._do_import(
            project_id=project_id,
            snapshot_set_id=snapshot_set_id,
            generation_id=False,
        )

    @api.model
    def import_from_project(self, generation_id, project_id):
        """
        Legacy API — import into a per-generation pool.

        Kept for backward compatibility with doc_generation_project_mixin.
        """
        return self._do_import(
            project_id=project_id,
            snapshot_set_id=False,
            generation_id=generation_id,
        )

    @api.model
    def _do_import(self, project_id, snapshot_set_id=False, generation_id=False):
        """Core import logic shared by both public APIs."""
        if not project_id:
            _logger.warning('_do_import: project_id is falsy — nothing imported')
            return {'imported': 0, 'skipped': 0}

        if 'project.task' not in self.env:
            _logger.warning(
                '_do_import: project.task model not available '
                '(project module not installed)'
            )
            return {'imported': 0, 'skipped': 0}

        Task = self.env['project.task'].sudo()

        if 'project.project' in self.env:
            project = self.env['project.project'].sudo().browse(project_id)
            if not project.exists():
                _logger.warning(
                    '_do_import: project.project id=%s does not exist', project_id
                )
                return {'imported': 0, 'skipped': 0}

        # Delete previous snapshots for this container
        if snapshot_set_id:
            self.search([('snapshot_set_id', '=', snapshot_set_id)]).unlink()
        else:
            self.search([('generation_id', '=', generation_id)]).unlink()

        # Load ALL tasks in the project (including archived) in one query
        all_tasks = Task.search(
            [('project_id', '=', project_id), ('active', 'in', [True, False])],
            order='id asc',
        )
        _logger.info(
            '_do_import: project_id=%s — found %s tasks',
            project_id, len(all_tasks),
        )

        task_by_id = {t.id: t for t in all_tasks}
        snap_by_task_id = {}
        imported = 0

        for task in all_tasks:
            # Calculate depth by walking the parent chain
            depth = 0
            current = task
            while current.parent_id and current.parent_id.id in task_by_id:
                depth += 1
                current = task_by_id[current.parent_id.id]
                if depth > 10:
                    break

            tag, clean = _strip_tag(task.name or '')

            parent_snap_id = False
            if task.parent_id and task.parent_id.id in snap_by_task_id:
                parent_snap_id = snap_by_task_id[task.parent_id.id]

            vals = {
                'original_task_id': task.id,
                'original_project_id': project_id,
                'name': task.name or '',
                'description_plain': _html_to_plain(task.description or ''),
                'module_tag': tag or False,
                'name_clean': clean or (task.name or ''),
                'depth': depth,
                'sequence': getattr(task, 'sequence', 10) or 10,
                'parent_snapshot_id': parent_snap_id or False,
            }
            if snapshot_set_id:
                vals['snapshot_set_id'] = snapshot_set_id
            else:
                vals['generation_id'] = generation_id

            snap = self.create(vals)
            snap_by_task_id[task.id] = snap.id
            imported += 1

        _logger.info(
            '_do_import: container=set:%s/gen:%s imported=%s',
            snapshot_set_id, generation_id, imported,
        )
        return {'imported': imported, 'skipped': 0}
