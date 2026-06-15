# -*- coding: utf-8 -*-
"""Project.task → doc enrichment service.

Enriches ``doc.module``, ``doc.menu`` and ``doc.function`` records with
human-written descriptions from Odoo ``project.task`` / sub-tasks.

Design principles
-----------------
* Best-effort only — silently skipped when project module absent / no task.
* Copy, don’t reference — text is copied at enrichment time.
* Never overwrite manual edits (overwrite=False by default).
* Precedence: manual > task > generated-default.

Task-name convention
--------------------
Any task whose name starts with ``[module_technical_name]`` is a candidate.
All such tasks are considered, and ALL their sub-tasks are pooled together
before matching against menus / functions.  This lets a team split the
description work across several parent tasks:

    [dpf_news] Информационный блок (задача 1)
        ├ [sub] All Posts — описание ...
        └ [sub] Создание новости ...

    [dpf_news] Технические детали (задача 2)
        └ [sub] Настройка ...

Sub-task description format (optional section markers improve splitting):

    Описание:
    Текст ...

    Требования:
    - ...

    Порядок:
    1. ...

    Результат:
    ...
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[([\w]+)\]\s*', flags=re.UNICODE)
_NOISE_RE = re.compile(
    r'ТЗ|ТС|§[\d\.]+|§\d|\[.*?\]|["\u00ab\u00bb()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)
_SECTION_RE = re.compile(
    r'^\s*(?P<key>\u041eписание|Требования|Порядок|Результат)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)


def _normalize(text):
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    text = _NOISE_RE.sub(' ', text)
    text = text.lower().strip()
    return re.sub(r'\s+', ' ', text)


def _similarity(a, b):
    """Jaccard word-overlap [0.0 – 1.0]."""
    tokens_a = set(_normalize(a).split())
    tokens_b = set(_normalize(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


class DocProjectEnricher(models.AbstractModel):
    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_module(self, doc_module, overwrite=False):
        """Обогатить doc_module данными из project.task.

        Три прохода:
          1. doc.module.description  ← описание родительского таска
          2. doc.menu.caption        ← лучший совпадающий сабтаск
          3. doc.function fields     ← лучший совпадающий сабтаск
        Сабтаски собираются со ВСЕХ родительских тасков с префиксом [module_name].
        """
        stats = {
            'module_enriched': False,
            'menus_enriched': 0,
            'skipped': 0,
            'functions_enriched': 0,
        }

        if not self._project_installed():
            return stats

        technical_name = doc_module.technical_name
        parent_tasks = self._find_all_parent_tasks(technical_name)

        if not parent_tasks:
            _logger.info(
                'doc.project.enricher: no tasks found for module %s', technical_name
            )
            return stats

        # Use the first (best) task for the module-level description
        stats['module_enriched'] = self._enrich_module_description(
            doc_module, parent_tasks[0], overwrite=overwrite
        )

        # Pool ALL subtasks from ALL matching parent tasks
        subtasks = self.env['project.task'].browse()
        for parent in parent_tasks:
            subtasks |= parent.child_ids.filtered(
                lambda t: (t.description or '').strip() or (t.name or '').strip()
            )

        _logger.info(
            'doc.project.enricher: module=%s parent_tasks=%s total_subtasks=%s',
            technical_name, len(parent_tasks), len(subtasks),
        )

        # Pass 2 — menu captions
        for menu in doc_module.menu_ids:
            if self._enrich_menu_caption(menu, subtasks, overwrite=overwrite):
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        # Pass 3 — function descriptions
        for func in doc_module.function_ids:
            if self._enrich_function(func, subtasks, overwrite=overwrite):
                stats['functions_enriched'] += 1

        _logger.info(
            'doc.project.enricher: module=%s enriched=%s menus=%s functions=%s skipped=%s',
            technical_name, stats['module_enriched'],
            stats['menus_enriched'], stats['functions_enriched'], stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------
    # Project check
    # ------------------------------------------------------------------

    def _project_installed(self):
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'project'), ('state', '=', 'installed')], limit=1
        )
        return bool(module)

    # ------------------------------------------------------------------
    # Task discovery — returns a LIST of all matching parent tasks
    # ------------------------------------------------------------------

    def _find_all_parent_tasks(self, technical_name):
        """Найти ВСЕ project.task с префиксом [technical_name].

        Возвращает список, отсортированный по убыванию сабтасков (больше → раньше),
        затем активные перед архивированными.
        """
        try:
            Task = self.env['project.task'].sudo()
        except KeyError:
            return []

        all_tasks = Task.search([
            ('name', 'like', '[%s]' % technical_name),
            ('active', 'in', [True, False]),
        ])

        candidates = [
            t for t in all_tasks
            if (lambda m: m and m.group(1).lower() == technical_name.lower())(
                _TAG_RE.match(t.name or '')
            )
        ]

        candidates.sort(key=lambda t: (-len(t.child_ids), 0 if t.active else 1))
        return candidates

    # ------------------------------------------------------------------
    # Text utilities
    # ------------------------------------------------------------------

    def _clean_html_to_text(self, html):
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

    def _best_subtask_match(self, name, subtasks, threshold=0.30):
        """Return (best_task, score) or (None, 0.0) if below threshold."""
        best_task = None
        best_score = 0.0
        for subtask in subtasks:
            score = _similarity(subtask.name or '', name or '')
            last = (subtask.name or '').split('/')[-1].strip()
            if last != subtask.name:
                score = max(score, _similarity(last, name or ''))
            if score > best_score:
                best_score = score
                best_task = subtask
        return (best_task, best_score) if best_score >= threshold else (None, 0.0)

    def _parse_subtask_sections(self, plain_text):
        """Разбить текст на секции по маркерам (Описание / Требования / Порядок / Результат)."""
        result = {'description': '', 'requirements': '', 'steps': '', 'result': ''}
        if not plain_text:
            return result
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
        # If no section markers, all text goes to description
        if not any([result['requirements'], result['steps'], result['result']]):
            result['description'] = plain_text.strip()
        return result

    # ------------------------------------------------------------------
    # Enrichment writes
    # ------------------------------------------------------------------

    def _enrich_module_description(self, doc_module, parent_task, overwrite=False):
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
        if not overwrite:
            src = menu.caption_source or 'generated'
            if src == 'manual':
                return False
            if src == 'task' and menu.caption:
                return False
        best_task, _score = self._best_subtask_match(
            menu.name or '', subtasks, threshold=threshold
        )
        if not best_task:
            last_segment = (menu.complete_name or '').split('/')[-1].strip()
            best_task, _score = self._best_subtask_match(
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
        """Обогатить doc.function из лучшего сабтаска (fuzzy Jaccard ≥ 0.30)."""
        best_task, _score = self._best_subtask_match(
            func.name or '', subtasks, threshold=threshold
        )
        if not best_task:
            return False
        raw_text = self._clean_html_to_text(best_task.description or '')
        if not raw_text:
            return False
        sections = self._parse_subtask_sections(raw_text)
        updates = {}
        if sections['description'] and (not func.description or overwrite):
            updates['description'] = sections['description']
        if sections['requirements'] and (not func.requirements or overwrite):
            updates['requirements'] = sections['requirements']
        if sections['steps'] and (not func.steps or overwrite):
            updates['steps'] = sections['steps']
        if sections['result'] and (not func.result or overwrite):
            updates['result'] = sections['result']
        if not updates:
            return False
        func.write(updates)
        return True
