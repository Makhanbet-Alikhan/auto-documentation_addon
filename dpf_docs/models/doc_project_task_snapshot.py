# -*- coding: utf-8 -*-
"""
Persistent snapshot of project.task data imported from the Project module.

Once tasks are imported here the enricher reads ONLY from these snapshot
records — no live queries against project.task happen during generation.
The Project module can therefore be uninstalled or its tasks deleted without
affecting existing enrichment.

Snapshot records are attached to a doc.generation run (many-to-one) so each
generation keeps its own isolated copy. They can be refreshed at any time via
the "Re-import from Project" button on the generation form.
"""
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Matches leading [tag] prefix, e.g. "[dpf_events] Создание мероприятий"
_TAG_RE = re.compile(r'^\[(?P<tag>[\w]+)\]\s*', flags=re.UNICODE)


def _strip_tag(name):
    """Return (tag, clean_name) from a task name like '[dpf_events] Some text'."""
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
    _order = 'generation_id, depth, sequence, id'

    generation_id = fields.Many2one(
        'doc.generation',
        string='Generation Run',
        required=True,
        ondelete='cascade',
        index=True,
    )

    # Original task identity — for traceability only, NOT a live FK
    original_task_id = fields.Integer(
        string='Original task.id',
        help='ID of the project.task at import time. For traceability only.',
    )
    original_project_id = fields.Integer(
        string='Original project.id',
        help='ID of the project.project at import time.',
    )

    # Task content snapshot
    name = fields.Char(string='Task Name', required=True)
    description_plain = fields.Text(
        string='Description (plain text)',
        help='HTML description stripped to plain text at import time.',
    )

    # Derived / indexed fields for fast lookup
    module_tag = fields.Char(
        string='Module Tag',
        index=True,
        help='Lower-cased [tag] extracted from the task name prefix at import time.',
    )
    name_clean = fields.Char(
        string='Clean Name',
        help='Task name with the [tag] prefix removed.',
    )

    # Tree structure mirrored from the original task hierarchy
    # depth 0 = top-level task in project ("main tasks" / parent containers)
    # depth 1 = child of main task  ("module parent task" tagged [dpf_events])
    # depth 2 = grandchild          ("functional subtask" with descriptions)
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

    @api.model
    def import_from_project(self, generation_id, project_id):
        """
        Pull all tasks from project_id into snapshot records for generation_id.

        Traverses the full task hierarchy:
            project.task (root tasks)                  depth=0
            └── project.task (module parent tasks)     depth=1  (tagged [dpf_events])
                 └── project.task (functional tasks)   depth=2  (contain descriptions)

        Safe to call multiple times — deletes previous snapshots first.

        Parameters
        ----------
        generation_id : int
        project_id    : int  (project.project id)

        Returns
        -------
        dict with keys 'imported' and 'skipped'
        """
        if not project_id:
            _logger.warning(
                'import_from_project: project_id is falsy — nothing imported'
            )
            return {'imported': 0, 'skipped': 0}

        if 'project.task' not in self.env:
            _logger.warning(
                'import_from_project: project.task model not available '
                '(project module not installed)'
            )
            return {'imported': 0, 'skipped': 0}

        Task = self.env['project.task'].sudo()

        # Verify the project exists before doing anything
        if 'project.project' in self.env:
            project = self.env['project.project'].sudo().browse(project_id)
            if not project.exists():
                _logger.warning(
                    'import_from_project: project.project id=%s does not exist',
                    project_id,
                )
                return {'imported': 0, 'skipped': 0}

        # Delete any previous snapshots for this generation
        self.search([('generation_id', '=', generation_id)]).unlink()

        # Load ALL tasks in the project (including archived) in one query
        all_tasks = Task.search(
            [('project_id', '=', project_id), ('active', 'in', [True, False])],
            order='id asc',
        )
        _logger.info(
            'import_from_project: project_id=%s — found %s tasks total',
            project_id, len(all_tasks),
        )

        task_by_id = {t.id: t for t in all_tasks}
        snap_by_task_id = {}   # original task.id → snapshot record id
        imported = 0

        for task in all_tasks:
            # Calculate depth by walking the parent chain
            depth = 0
            current = task
            while current.parent_id and current.parent_id.id in task_by_id:
                depth += 1
                current = task_by_id[current.parent_id.id]
                if depth > 10:   # safety guard against accidental cycles
                    break

            tag, clean = _strip_tag(task.name or '')

            # Resolve parent snapshot id (if parent was already processed)
            parent_snap_id = False
            if task.parent_id and task.parent_id.id in snap_by_task_id:
                parent_snap_id = snap_by_task_id[task.parent_id.id]

            snap = self.create({
                'generation_id': generation_id,
                'original_task_id': task.id,
                'original_project_id': project_id,
                'name': task.name or '',
                'description_plain': _html_to_plain(task.description or ''),
                'module_tag': tag or False,
                'name_clean': clean or (task.name or ''),
                'depth': depth,
                'sequence': getattr(task, 'sequence', 10) or 10,
                'parent_snapshot_id': parent_snap_id or False,
            })
            snap_by_task_id[task.id] = snap.id
            imported += 1

        _logger.info(
            'import_from_project: generation_id=%s imported=%s',
            generation_id, imported,
        )
        return {'imported': imported, 'skipped': 0}
