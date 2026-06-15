# -*- coding: utf-8 -*-
"""Project.task → doc enrichment service.

Task structure expected in the project:

  Project (е.г. «ЦПФ Этап 2»)
  └── [Main task] Разработка модулей ...   (main task, ~53 штуки)
       ├── [dpf_events] Создание мероприятий ...  (module parent task)
       │    ├── Subtask 1  (contains Описание/Требования/Порядок/Результат)
       │    └── Subtask 2
       ├── [dpf_news] ...
       │    └── ...
       └── ...
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
    r'^\s*(?P<key>Описание|Требования|Порядок|Результат)\s*:?\s*$',
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

    def enrich_module(self, doc_module, overwrite=False, project_id=False):
        stats = {
            'module_enriched': False,
            'menus_enriched': 0,
            'skipped': 0,
            'functions_enriched': 0,
        }

        if not self._project_installed():
            _logger.warning('enrich_module: project module is NOT installed')
            return stats

        technical_name = doc_module.technical_name
        display_name = doc_module.name or ''

        # --- DEBUG: dump first 10 tasks in the project so user can see structure ---
        self._debug_dump_project_tasks(project_id, technical_name)

        # Step 1: find module-level parent tasks tagged [technical_name]
        module_parent_tasks = self._find_module_parent_tasks(
            technical_name, display_name=display_name, project_id=project_id
        )

        if module_parent_tasks:
            subtasks = self.env['project.task'].browse()
            for parent in module_parent_tasks:
                children = parent.child_ids.filtered(
                    lambda t: (t.description or '').strip() or (t.name or '').strip()
                )
                subtasks |= children

            if not subtasks:
                subtasks = self.env['project.task'].browse(
                    [t.id for t in module_parent_tasks]
                )

            stats['module_enriched'] = self._enrich_module_description(
                doc_module, module_parent_tasks[0], overwrite=overwrite
            )

            _logger.info(
                '[ENRICH TAG] module=%s parent_tasks=%s subtasks=%s',
                technical_name, len(module_parent_tasks), len(subtasks),
            )
        else:
            subtasks = self._find_subtasks_fuzzy(
                technical_name, display_name, project_id=project_id
            )
            if subtasks and len(subtasks) > 0:
                stats['module_enriched'] = self._enrich_module_description(
                    doc_module, subtasks[0], overwrite=overwrite
                )
            _logger.info(
                '[ENRICH FUZZY] module=%s subtasks=%s',
                technical_name, len(subtasks),
            )

        if not subtasks:
            _logger.warning(
                '[ENRICH] No subtasks found for module=%s (project_id=%s). '
                'Check that tasks are named [%s] ... or contain the module name.',
                technical_name, project_id, technical_name,
            )
            return stats

        # Log the subtask pool being used
        _logger.info(
            '[ENRICH] Using %s subtasks for module=%s: %s',
            len(subtasks), technical_name,
            [t.name for t in list(subtasks)[:10]],
        )

        for menu in doc_module.menu_ids:
            if self._enrich_menu_caption(menu, subtasks, overwrite=overwrite):
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        for func in doc_module.function_ids:
            if self._enrich_function(func, subtasks, overwrite=overwrite):
                stats['functions_enriched'] += 1

        _logger.info(
            '[ENRICH] DONE module=%s: module_enriched=%s menus=%s functions=%s skipped=%s',
            technical_name, stats['module_enriched'],
            stats['menus_enriched'], stats['functions_enriched'], stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------
    # Debug helper
    # ------------------------------------------------------------------

    def _debug_dump_project_tasks(self, project_id, technical_name):
        """Log first 20 task names in the project to help debug naming issues."""
        if not project_id:
            _logger.warning('[ENRICH DEBUG] project_id is False/0 — cannot search tasks!')
            return
        try:
            tasks = self.env['project.task'].sudo().search(
                [('project_id', '=', project_id), ('active', 'in', [True, False])],
                limit=20, order='id asc',
            )
            _logger.info(
                '[ENRICH DEBUG] project_id=%s, searching for module=%s. '
                'First %s tasks in project: %s',
                project_id, technical_name, len(tasks),
                [t.name for t in tasks],
            )
        except Exception as e:
            _logger.warning('[ENRICH DEBUG] Could not dump tasks: %s', e)

    # ------------------------------------------------------------------
    # Project check
    # ------------------------------------------------------------------

    def _project_installed(self):
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'project'), ('state', '=', 'installed')], limit=1
        )
        return bool(module)

    # ------------------------------------------------------------------
    # Task discovery
    # ------------------------------------------------------------------

    def _find_module_parent_tasks(self, technical_name, display_name='', project_id=False):
        """Find tasks tagged [technical_name] OR matching display_name anywhere in the project."""
        try:
            Task = self.env['project.task'].sudo()
        except KeyError:
            return []

        domain_base = [('active', 'in', [True, False])]
        if project_id:
            domain_base.append(('project_id', '=', project_id))

        # Search 1: by [technical_name] tag in task name
        candidates_tag = Task.search(
            [('name', 'ilike', '[%s]' % technical_name)] + domain_base
        )
        _logger.info(
            '_find_module_parent_tasks: tag search "[%s]" found %s candidates',
            technical_name, len(candidates_tag),
        )

        # Exact tag match
        result = []
        for t in candidates_tag:
            m = _TAG_RE.match(t.name or '')
            if m and m.group(1).lower() == technical_name.lower():
                result.append(t)
                _logger.info('  TAG MATCH: id=%s name=%s', t.id, t.name)

        if result:
            result.sort(key=lambda t: (-len(t.child_ids), 0 if t.active else 1))
            return result

        # Search 2: by display_name (e.g. "DPF News" or "DPF Events")
        if display_name:
            candidates_dn = Task.search(
                [('name', 'ilike', display_name)] + domain_base
            )
            _logger.info(
                '_find_module_parent_tasks: display_name search "%s" found %s candidates',
                display_name, len(candidates_dn),
            )
            for t in candidates_dn:
                if t not in result:
                    result.append(t)
                    _logger.info('  DISPLAY_NAME MATCH: id=%s name=%s', t.id, t.name)

        if result:
            result.sort(key=lambda t: (-len(t.child_ids), 0 if t.active else 1))
            return result

        _logger.info(
            '_find_module_parent_tasks: module=%s — nothing found (project_id=%s)',
            technical_name, project_id,
        )
        return []

    def _find_subtasks_fuzzy(self, technical_name, display_name='', project_id=False):
        """Fallback: find subtasks via keyword matching."""
        try:
            Task = self.env['project.task'].sudo()
        except KeyError:
            return self.env['project.task'].browse()

        base_domain = [('active', 'in', [True, False])]
        if project_id:
            base_domain.append(('project_id', '=', project_id))

        keywords = [k for k in [
            technical_name,
            technical_name.replace('_', ' '),
            display_name,
        ] if k]

        seen_ids = set()
        parent_tasks = []
        for kw in keywords:
            found = Task.search([('name', 'ilike', kw)] + base_domain)
            _logger.info(
                '_find_subtasks_fuzzy: keyword="%s" found %s tasks: %s',
                kw, len(found), [t.name for t in found[:5]],
            )
            for t in found:
                if t.id not in seen_ids:
                    seen_ids.add(t.id)
                    parent_tasks.append(t)

        if not parent_tasks and project_id:
            # Project-wide fallback: ALL tasks in project
            all_tasks = list(Task.search(
                [('project_id', '=', project_id), ('active', 'in', [True, False])]
            ))
            _logger.info(
                '_find_subtasks_fuzzy: project-wide fallback, total tasks=%s',
                len(all_tasks),
            )
            parent_tasks = all_tasks

        subtasks = self.env['project.task'].browse()
        for parent in parent_tasks:
            children = parent.child_ids.filtered(
                lambda t: (t.description or '').strip() or (t.name or '').strip()
            )
            subtasks |= children

        if not subtasks and parent_tasks:
            subtasks = self.env['project.task'].browse([t.id for t in parent_tasks])

        return subtasks

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
        best_task = None
        best_score = 0.0
        for subtask in subtasks:
            score = _similarity(subtask.name or '', name or '')
            m = _TAG_RE.match(subtask.name or '')
            if m:
                clean_name = (subtask.name or '')[m.end():].strip()
                score = max(score, _similarity(clean_name, name or ''))
            if score > best_score:
                best_score = score
                best_task = subtask
        if best_task:
            _logger.info(
                '_best_subtask_match: func/menu="%s" -> best task="%s" score=%.2f (threshold=%.2f) match=%s',
                name, best_task.name, best_score, threshold, best_score >= threshold,
            )
        return (best_task, best_score) if best_score >= threshold else (None, 0.0)

    def _parse_subtask_sections(self, plain_text):
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
