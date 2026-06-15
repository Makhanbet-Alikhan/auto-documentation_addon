# -*- coding: utf-8 -*-
"""Project.task → doc enrichment service.

This module enriches ``doc.module``, ``doc.menu``, and ``doc.function``
records with human-written descriptions sourced from Odoo
``project.task`` / sub-tasks.

Design principles
-----------------
* **Best-effort only.** If the ``project`` module is not installed, or no
  matching task is found, enrichment is silently skipped — generation still
  works without it.
* **Copy, don’t reference.** Descriptions are *copied* into doc records at
  enrichment time so the documentation survives task deletion.
* **Never overwrite manual edits.** Fields edited by a human are left
  untouched unless *overwrite=True* is explicitly passed.
* **Precedence**: manual > task > generated-default.

Task name convention
--------------------
Tasks whose name starts with ``[module_technical_name]`` are considered
candidates for that module.  Sub-tasks of such a parent task are mapped to
menus *and* functions by fuzzy name matching.

    [dpf_portal] Публичный каталог фондов
        └ [sub] All Posts — список всех новостей  (матчится на меню + функцию)

Sub-task description format (optional, improves splitting):

    Описание:
    Текст описания...

    Требования:
    - права доступа...

    Порядок:
    1. Открыть меню...
    2. Нажать кнопку...

    Результат:
    Запись сохранена.

If no section markers are found the full text goes into description.
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

# Regex that extracts the technical name from "[module_name] some title"
_TAG_RE = re.compile(r'^\[([\w]+)\]\s*', flags=re.UNICODE)

# Tokens to strip when normalising names for fuzzy matching
_NOISE_RE = re.compile(
    r'ТЗ|ТС|§[\d\.]+|§\d|\[.*?\]|["\u00ab\u00bb()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)

# Section header markers inside sub-task descriptions
_SECTION_RE = re.compile(
    r'^\s*(?P<key>\u041eписание|Требования|Порядок|Результат)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)


def _normalize(text):
    """Lower-case, strip accents, remove noise tokens, collapse spaces."""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    text = _NOISE_RE.sub(' ', text)
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _similarity(a, b):
    """Lightweight Jaccard word-overlap score [0.0 – 1.0]."""
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

        Enriches in three passes:
          1. doc.module.description  ← parent task description
          2. doc.menu.caption        ← best-matching sub-task name/description
          3. doc.function fields     ← best-matching sub-task description
             (description, requirements, steps, result)

        :param doc_module: a ``doc.module`` record.
        :param overwrite: if True, overwrite existing non-empty values.
        :returns: dict with enrichment statistics.
        """
        stats = {
            'module_enriched': False,
            'menus_enriched': 0,
            'skipped': 0,
            'functions_enriched': 0,
        }

        if not self._project_installed():
            _logger.info(
                'doc.project.enricher: project module not installed, skipping.'
            )
            return stats

        technical_name = doc_module.technical_name
        parent_task = self._find_parent_task(technical_name)

        if not parent_task:
            _logger.info(
                'doc.project.enricher: no task found for module %s', technical_name
            )
            return stats

        # Pass 1 — module description
        stats['module_enriched'] = self._enrich_module_description(
            doc_module, parent_task, overwrite=overwrite
        )

        # Collect sub-tasks that have any useful text
        subtasks = parent_task.child_ids.filtered(
            lambda t: (t.description or '').strip() or (t.name or '').strip()
        )

        # Pass 2 — menu captions
        for menu in doc_module.menu_ids:
            enriched = self._enrich_menu_caption(
                menu, subtasks, overwrite=overwrite
            )
            if enriched:
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        # Pass 3 — function descriptions (the main new behaviour)
        for func in doc_module.function_ids:
            enriched = self._enrich_function(
                func, subtasks, overwrite=overwrite
            )
            if enriched:
                stats['functions_enriched'] += 1

        _logger.info(
            'doc.project.enricher: module=%s enriched=%s menus=%s '
            'functions=%s skipped=%s',
            technical_name,
            stats['module_enriched'],
            stats['menus_enriched'],
            stats['functions_enriched'],
            stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------
    # Internal helpers — project check
    # ------------------------------------------------------------------

    def _project_installed(self):
        """Return True if the project module is installed."""
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'project'), ('state', '=', 'installed')], limit=1
        )
        return bool(module)

    def _find_parent_task(self, technical_name):
        """Find the best matching project.task for *technical_name*.

        Prefers tasks with sub-tasks (richer context), then active ones.
        Returns a single ``project.task`` browse record or None.
        """
        try:
            Task = self.env['project.task'].sudo()
        except KeyError:
            return None

        all_tasks = Task.search([
            ('name', 'like', '[%s]' % technical_name),
            ('active', 'in', [True, False]),
        ])

        candidates = []
        for task in all_tasks:
            m = _TAG_RE.match(task.name or '')
            if m and m.group(1).lower() == technical_name.lower():
                candidates.append(task)

        if not candidates:
            return None

        candidates.sort(
            key=lambda t: (
                -len(t.child_ids),
                0 if t.active else 1,
            )
        )
        return candidates[0]

    # ------------------------------------------------------------------
    # Internal helpers — text utilities
    # ------------------------------------------------------------------

    def _clean_html_to_text(self, html):
        """Strip HTML tags and decode basic entities; return plain text."""
        if not html:
            return ''
        text = re.sub(r'<[^>]+>', ' ', html)
        text = text.replace('&amp;', '&').replace('&lt;', '<')
        text = text.replace('&gt;', '>').replace('&nbsp;', ' ')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _best_subtask_match(self, name, subtasks, threshold=0.30):
        """Return (best_task, score) for *name* against *subtasks*.

        Compares *name* against both subtask.name and the last segment of
        subtask.name (after '/'). Falls back to (None, 0.0).
        The threshold is intentionally lower (0.30) than for menus because
        function names are often short summaries of the task title.
        """
        best_task = None
        best_score = 0.0
        for subtask in subtasks:
            score = _similarity(subtask.name or '', name or '')
            # also try last path segment if name contains '/'
            last = (subtask.name or '').split('/')[-1].strip()
            if last != subtask.name:
                score = max(score, _similarity(last, name or ''))
            if score > best_score:
                best_score = score
                best_task = subtask
        if best_score < threshold:
            return None, 0.0
        return best_task, best_score

    def _parse_subtask_sections(self, plain_text):
        """Разбить plain text сабтаска на секции.

        Если текст содержит маркеры "Описание:", "Требования:",
        "Порядок:", "Результат:" — разбивает по ним.
        Иначе весь текст идёт в секцию 'description'.

        :returns: dict with keys description, requirements, steps, result
                  (all str, may be empty).
        """
        result = {'description': '', 'requirements': '', 'steps': '', 'result': ''}
        if not plain_text:
            return result

        # Key mapping from Russian marker to dict key
        _KEY_MAP = {
            'описание': 'description',
            'требования': 'requirements',
            'порядок': 'steps',
            'результат': 'result',
        }

        lines = plain_text.splitlines()
        current_key = 'description'
        buckets = {k: [] for k in result}

        for line in lines:
            m = _SECTION_RE.match(line)
            if m:
                current_key = _KEY_MAP.get(m.group('key').lower(), 'description')
            else:
                buckets[current_key].append(line)

        for k, bucket in buckets.items():
            result[k] = '\n'.join(bucket).strip()

        # If no section markers found, everything is in description
        if not any([result['requirements'], result['steps'], result['result']]):
            result['description'] = plain_text.strip()

        return result

    # ------------------------------------------------------------------
    # Internal helpers — enrichment writes
    # ------------------------------------------------------------------

    def _enrich_module_description(self, doc_module, parent_task, overwrite=False):
        """Copy the task description into doc_module.description."""
        if doc_module.description and not overwrite:
            return False

        raw_desc = self._clean_html_to_text(parent_task.description or '')
        if not raw_desc:
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
        """Try to find a matching sub-task for *menu* and copy its description."""
        if not overwrite:
            src = menu.caption_source or 'generated'
            if src == 'manual':
                return False
            if src == 'task' and menu.caption:
                return False

        best_task, score = self._best_subtask_match(
            menu.name or '', subtasks, threshold=threshold
        )
        if not best_task:
            # also try complete_name last segment
            last_segment = (menu.complete_name or '').split('/')[-1].strip()
            best_task, score = self._best_subtask_match(
                last_segment, subtasks, threshold=threshold
            )
        if not best_task:
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

    def _enrich_function(self, func, subtasks, overwrite=False, threshold=0.30):
        """Обогатить doc.function данными из лучшего совпадающего сабтаска.

        Совпадение ищется по имени функции (fuzzy Jaccard ≥ 0.30).
        Текст сабтаска разбивается на секции (Описание / Требования / Порядок / Результат).
        Существующие значения не перезаписываются (если overwrite=False).

        Returns True if any field was written.
        """
        func_name = func.name or ''
        best_task, _score = self._best_subtask_match(
            func_name, subtasks, threshold=threshold
        )
        if not best_task:
            return False

        raw_text = self._clean_html_to_text(best_task.description or '')
        if not raw_text:
            # If subtask has no description, skip — name alone is not enough
            # for a meaningful function enrichment (name already in func.name).
            return False

        sections = self._parse_subtask_sections(raw_text)

        updates = {}

        # description
        if sections['description'] and (not func.description or overwrite):
            updates['description'] = sections['description']

        # requirements
        if sections['requirements'] and (not func.requirements or overwrite):
            updates['requirements'] = sections['requirements']

        # steps (stored as \n-separated lines in func.steps)
        if sections['steps'] and (not func.steps or overwrite):
            updates['steps'] = sections['steps']

        # result
        if sections['result'] and (not func.result or overwrite):
            updates['result'] = sections['result']

        if not updates:
            return False

        func.write(updates)
        return True
