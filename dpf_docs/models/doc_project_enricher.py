# -*- coding: utf-8 -*-
"""Project.task → doc enrichment service.

This module enriches ``doc.module`` and ``doc.menu`` records with human-written
descriptions sourced from Odoo ``project.task`` / ``project.task`` sub-tasks.

Design principles
-----------------
* **Best-effort only.** If the ``project`` module is not installed, or no
  matching task is found, enrichment is silently skipped — generation still
  works without it.
* **Copy, don't reference.** Descriptions are *copied* into ``doc.module`` /
  ``doc.menu`` at enrichment time.  That way the documentation survives even
  after the original task is closed and deleted.
* **Never overwrite manual edits.** Any field that was edited by a human
  (``caption_source == 'manual'`` for menus, non-empty ``description`` for
  modules) is left untouched unless *overwrite=True* is explicitly passed.
* **Precedence**: manual > task > generated-default.

Task name convention
--------------------
Tasks whose name starts with ``[module_technical_name]`` (square brackets) are
considered candidates for that module.  Example:

    [dpf_portal] Публичный каталог фондов: поиск и просмотр материалов

Sub-tasks of such a parent task are mapped to menus by fuzzy name matching.
"""
import logging
import re
import unicodedata

from odoo import _, models, fields

_logger = logging.getLogger(__name__)

# Regex that extracts the technical name from "[module_name] some title"
_TAG_RE = re.compile(r'^\[([\w]+)\]\s*', flags=re.UNICODE)

# Tokens to strip when normalising names for fuzzy matching
_NOISE_RE = re.compile(
    r'ТЗ|ТС|§[\d\.]+|§\d|\[.*?\]|["«»()/\\,\.\!\?\-\–—]',
    flags=re.UNICODE,
)


def _normalize(text):
    """Lower-case, strip accents, remove noise tokens, collapse spaces."""
    if not text:
        return ''
    # Unicode NFC normalisation
    text = unicodedata.normalize('NFC', text)
    text = _NOISE_RE.sub(' ', text)
    text = text.lower().strip()
    # collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text


def _similarity(a, b):
    """Very lightweight word-overlap score [0.0 – 1.0].

    Returns the Jaccard similarity of the two normalised token sets.  Good
    enough for short menu names (3-6 words).
    """
    tokens_a = set(_normalize(a).split())
    tokens_b = set(_normalize(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


class DocProjectEnricher(models.AbstractModel):
    """Service that pulls context from project.task into documentation records.

    Usage from Python::

        self.env['doc.project.enricher'].enrich_module(doc_module)

    Or from the UI via ``action_enrich_from_project`` on ``doc.module``.
    """

    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_module(self, doc_module, overwrite=False):
        """Enrich *doc_module* with descriptions from project.task.

        :param doc_module: a ``doc.module`` record (browse object).
        :param overwrite: if True, overwrite existing non-empty descriptions.
        :returns: dict with enrichment statistics.
        """
        stats = {'module_enriched': False, 'menus_enriched': 0, 'skipped': 0}

        if not self._project_installed():
            _logger.info(
                'doc.project.enricher: project module not installed, skipping enrichment.'
            )
            return stats

        technical_name = doc_module.technical_name
        parent_task = self._find_parent_task(technical_name)

        if not parent_task:
            _logger.info(
                'doc.project.enricher: no task found for module %s', technical_name
            )
            return stats

        # ---- enrich module description --------------------------------
        stats['module_enriched'] = self._enrich_module_description(
            doc_module, parent_task, overwrite=overwrite
        )

        # ---- enrich menu captions from sub-tasks ----------------------
        subtasks = parent_task.child_ids.filtered(lambda t: t.description)
        for menu in doc_module.menu_ids:
            enriched = self._enrich_menu_caption(
                menu, subtasks, overwrite=overwrite
            )
            if enriched:
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        _logger.info(
            'doc.project.enricher: module=%s enriched=%s menus=%s skipped=%s',
            technical_name,
            stats['module_enriched'],
            stats['menus_enriched'],
            stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_installed(self):
        """Return True if the project module is installed."""
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'project'), ('state', '=', 'installed')], limit=1
        )
        return bool(module)

    def _find_parent_task(self, technical_name):
        """Find the best matching project.task for *technical_name*.

        Strategy:
        1. Look for tasks whose name starts with ``[technical_name]``.
        2. Among candidates, prefer tasks that have sub-tasks (richer context).
        3. Among equal candidates, prefer active ones.

        Returns a single ``project.task`` browse record or None.
        """
        try:
            Task = self.env['project.task'].sudo()
        except KeyError:
            return None

        all_tasks = Task.search([
            ('name', 'like', '[%s]' % technical_name),
            ('active', 'in', [True, False]),  # also archived
        ])

        candidates = []
        for task in all_tasks:
            m = _TAG_RE.match(task.name or '')
            if m and m.group(1).lower() == technical_name.lower():
                candidates.append(task)

        if not candidates:
            return None

        # Sort: prefer tasks with sub-tasks, then active ones
        candidates.sort(
            key=lambda t: (
                -len(t.child_ids),  # more sub-tasks first
                0 if t.active else 1,  # active first
            )
        )
        return candidates[0]

    def _clean_html_to_text(self, html):
        """Strip HTML tags and decode basic entities for storage as plain text."""
        if not html:
            return ''
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Decode common entities
        text = text.replace('&amp;', '&').replace('&lt;', '<')
        text = text.replace('&gt;', '>').replace('&nbsp;', ' ')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        # Collapse whitespace
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _enrich_module_description(self, doc_module, parent_task, overwrite=False):
        """Copy the task description into doc_module.description.

        Returns True if the description was actually written.
        """
        # Skip if description already set and overwrite not requested
        if doc_module.description and not overwrite:
            return False

        raw_desc = self._clean_html_to_text(parent_task.description or '')
        if not raw_desc:
            # Fall back to the task title itself as a one-liner description
            raw_desc = parent_task.name or ''
            m = _TAG_RE.match(raw_desc)
            if m:
                raw_desc = raw_desc[m.end():].strip()

        if not raw_desc:
            return False

        doc_module.write({
            'description': raw_desc,
            'project_context_source': parent_task.name,
            'project_context_note': _(
                'Описание импортировано из задачи проекта: "%s" (id=%s)'
            ) % (parent_task.name, parent_task.id),
            'project_task_name_snapshot': parent_task.name,
        })
        return True

    def _enrich_menu_caption(self, menu, subtasks, overwrite=False, threshold=0.35):
        """Try to find a matching sub-task for *menu* and copy its description.

        The matching score must exceed *threshold* (Jaccard similarity).
        Returns True if a caption was written.
        """
        # Skip if caption was set manually or already from a task
        if not overwrite:
            src = menu.caption_source or 'generated'
            if src == 'manual':
                return False
            if src == 'task' and menu.caption:
                return False

        best_task = None
        best_score = 0.0

        for subtask in subtasks:
            # Score against menu name
            score = _similarity(subtask.name or '', menu.name or '')
            # Bonus: also check against complete_name last segment
            if menu.complete_name:
                last_segment = (menu.complete_name or '').split('/')[-1].strip()
                score = max(score, _similarity(subtask.name or '', last_segment))
            if score > best_score:
                best_score = score
                best_task = subtask

        if not best_task or best_score < threshold:
            return False

        caption_text = self._clean_html_to_text(best_task.description or '')
        if not caption_text:
            caption_text = best_task.name or ''
            m = _TAG_RE.match(caption_text)
            if m:
                caption_text = caption_text[m.end():].strip()

        if not caption_text:
            return False

        menu.write({
            'caption': caption_text,
            'caption_source': 'task',
            'caption_task_name_snapshot': best_task.name,
        })
        return True
