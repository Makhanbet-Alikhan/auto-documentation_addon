# -*- coding: utf-8 -*-
"""
Persistent snapshot of project.task data.

Each record represents one task captured from project.task at import time.
All data is stored as plain text — no live foreign keys to project.task.
Once imported the snapshot survives project/task deletion.

Parent linkage
--------------
Each snapshot can belong to EITHER:
  * snapshot_set_id  (doc.project.snapshot.set)  — global, shared pool (preferred)
  * generation_id    (doc.generation)             — legacy per-generation pool

Import strategy
---------------
_do_import downloads ALL tasks in a project in a SINGLE query:
  * Main tasks (depth=0)
  * Their direct subtasks (depth=1)
  * Subtasks of subtasks (depth=2+)
  ... up to any nesting depth.

For the ЦПФ Этап 2 case this means 53 root tasks + 80 subtasks = 133 records.
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
    """One task snapshot — fully self-contained copy of project.task data."""

    _name = 'doc.project.task.snapshot'
    _description = 'Auto Doc - Project Task Snapshot'
    _order = 'depth asc, sequence asc, id asc'

    # ------------------------------------------------------------------ #
    # Parent container (exactly one is set)                                #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Identity — traceability only, NOT live FKs                          #
    # ------------------------------------------------------------------ #

    original_task_id = fields.Integer(
        string='Original task.id',
        index=True,
        help='ID of the original project.task record. Used only for upsert matching.',
    )
    original_project_id = fields.Integer(
        string='Original project.id',
    )

    # ------------------------------------------------------------------ #
    # Task content (full copy at import time)                             #
    # ------------------------------------------------------------------ #

    name = fields.Char(string='Task Name', required=True)
    description_plain = fields.Text(
        string='Description (plain text)',
        help='HTML description stripped to plain text at import time.',
    )
    module_tag = fields.Char(
        string='Module Tag',
        index=True,
        help='Lower-cased [tag] extracted from the task name prefix. E.g. "dpf_docs".',
    )
    name_clean = fields.Char(
        string='Clean Name',
        help='Task name with the [tag] prefix stripped.',
    )

    # ------------------------------------------------------------------ #
    # Tree structure                                                       #
    # ------------------------------------------------------------------ #

    depth = fields.Integer(
        string='Depth',
        default=0,
        help='0 = root/main task, 1 = direct subtask, 2 = subtask of subtask, …',
    )
    sequence = fields.Integer(string='Sequence', default=10)
    parent_snapshot_id = fields.Many2one(
        'doc.project.task.snapshot',
        string='Parent Snapshot',
        ondelete='set null',
        index=True,
    )
    child_snapshot_ids = fields.One2many(
        'doc.project.task.snapshot',
        'parent_snapshot_id',
        string='Child Snapshots',
    )

    # ------------------------------------------------------------------ #
    # Public import API                                                    #
    # ------------------------------------------------------------------ #

    @api.model
    def import_into_set(self, snapshot_set_id, project_id):
        """
        Import ALL tasks from project_id into a global snapshot set.

        Downloads:
          - All root/main tasks (tasks without parent or parent outside project)
          - ALL subtasks at any nesting depth

        The import is a full refresh: previous snapshots for this set are
        deleted first.  Safe to call multiple times (idempotent).

        Returns dict:
          {'imported': N, 'root_tasks': N, 'subtasks': N, 'skipped': 0}
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
        Kept for backward compatibility.
        """
        return self._do_import(
            project_id=project_id,
            snapshot_set_id=False,
            generation_id=generation_id,
        )

    # ------------------------------------------------------------------ #
    # Core import logic                                                    #
    # ------------------------------------------------------------------ #

    @api.model
    def _do_import(self, project_id, snapshot_set_id=False, generation_id=False):
        """
        Download all tasks (root + ALL subtasks) from project_id and store
        them as snapshot records.

        Strategy
        --------
        1. Load ALL project.task records where project_id = project_id in ONE
           query.  This naturally includes subtasks because Odoo stores
           subtasks under the same project_id.
        2. Build a task-id → task dict for O(1) parent lookups.
        3. For each task: walk the parent chain to determine depth.
        4. Create snapshot records in task-id order so parents are always
           created before their children (Odoo's search returns by id asc).
        """
        if not project_id:
            _logger.warning('_do_import: project_id is falsy — nothing imported')
            return {'imported': 0, 'root_tasks': 0, 'subtasks': 0, 'skipped': 0}

        if 'project.task' not in self.env:
            _logger.warning(
                '_do_import: project.task model not available '
                '(Projects module not installed).'
            )
            return {'imported': 0, 'root_tasks': 0, 'subtasks': 0, 'skipped': 0}

        Task = self.env['project.task'].sudo()

        # Verify project exists (optional — if project module present)
        if 'project.project' in self.env:
            project = self.env['project.project'].sudo().browse(project_id)
            if not project.exists():
                _logger.warning(
                    '_do_import: project.project id=%s does not exist.', project_id
                )
                return {'imported': 0, 'root_tasks': 0, 'subtasks': 0, 'skipped': 0}

        # ---- Full refresh: remove previous snapshots for this container ----
        if snapshot_set_id:
            existing = self.search([('snapshot_set_id', '=', snapshot_set_id)])
        else:
            existing = self.search([('generation_id', '=', generation_id)])
        if existing:
            _logger.info(
                '_do_import: deleting %s existing snapshots before re-import.',
                len(existing),
            )
            existing.unlink()

        # ---- Load ALL tasks in project in one query ------------------------
        # Odoo stores subtasks with the same project_id as the parent.
        # Using active_test=False ensures archived tasks are also captured.
        all_tasks = Task.with_context(active_test=False).search(
            [('project_id', '=', project_id)],
            order='id asc',
        )
        total_found = len(all_tasks)
        _logger.info(
            '_do_import: project_id=%s — found %s tasks (including subtasks)',
            project_id, total_found,
        )

        if not all_tasks:
            return {'imported': 0, 'root_tasks': 0, 'subtasks': 0, 'skipped': 0}

        # Build lookup dict for O(1) parent chain walking
        task_by_id = {t.id: t for t in all_tasks}

        # snap_by_task_id: task.id -> snapshot.id  (populated as we create)
        snap_by_task_id = {}
        imported = 0
        root_count = 0
        subtask_count = 0

        for task in all_tasks:
            # ---- Determine depth by walking the parent chain ----
            depth = 0
            current = task
            visited = set()  # Guard against circular refs (shouldn't happen but safe)
            while (
                current.parent_id
                and current.parent_id.id in task_by_id
                and current.parent_id.id not in visited
            ):
                visited.add(current.id)
                depth += 1
                current = task_by_id[current.parent_id.id]
                if depth > 20:  # Hard cap — more than 20 nesting levels is pathological
                    _logger.warning(
                        '_do_import: task id=%s depth exceeded 20, capping.', task.id
                    )
                    break

            # ---- Resolve parent snapshot id ----
            parent_snap_id = False
            if task.parent_id and task.parent_id.id in snap_by_task_id:
                parent_snap_id = snap_by_task_id[task.parent_id.id]

            # ---- Extract tag and clean name ----
            tag, clean = _strip_tag(task.name or '')

            # ---- Build vals ----
            vals = {
                'original_task_id': task.id,
                'original_project_id': project_id,
                'name': task.name or '(unnamed)',
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
            if depth == 0:
                root_count += 1
            else:
                subtask_count += 1

        _logger.info(
            '_do_import: DONE  container=set:%s/gen:%s  '
            'total=%s  root=%s  subtasks=%s',
            snapshot_set_id, generation_id,
            imported, root_count, subtask_count,
        )
        return {
            'imported': imported,
            'root_tasks': root_count,
            'subtasks': subtask_count,
            'skipped': 0,
        }
