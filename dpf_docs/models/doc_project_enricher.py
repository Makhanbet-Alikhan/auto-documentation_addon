# -*- coding: utf-8 -*-
"""
Project task enricher — reads exclusively from doc.project.task.snapshot.

The enricher NO LONGER queries project.task directly.  All task data must be
pre-imported via:
    self.env['doc.project.task.snapshot'].import_from_project(generation_id, project_id)

Name-matching fix
-----------------
The original enricher broke because:
  1. project_task_project_id is an Integer field whose default is 0.
     The domain ('project_id', '=', 0) matches NO task, so every enrichment
     silently returned empty results.
  2. Even when project_id was correct the enricher had to re-query live
     project.task every time, meaning deleted tasks = no enrichment.

Now matching works as follows:
  - At import time, doc.project.task.snapshot stores the lower-cased [tag]
    extracted from each task name as the 'module_tag' field.
  - technical_name='dpf_events'  matches snapshot.module_tag='dpf_events'  (exact)
  - display_name='DPF Events — Event Management Router' is a secondary fuzzy
    fallback via Jaccard word overlap.
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[([\w]+)\]\s*', flags=re.UNICODE)
_NOISE_RE = re.compile(
    r'\u0422\u0417|\u0422\u0421|§[\d\.]+|§\d|\[.*?\]|["\u00ab\u00bb()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)
_SECTION_RE = re.compile(
    r'^\s*(?P<key>\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435|\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f|\u041f\u043e\u0440\u044f\u0434\u043e\u043a|\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)
_CREATE_RE = re.compile(
    r'\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0435\s+\u0437\u0430\u043f\u0438\u0441\u0438',
    flags=re.UNICODE | re.IGNORECASE,
)


def _normalize(text):
    """Lowercase, NFC-normalize, strip noise characters, collapse whitespace."""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    text = _NOISE_RE.sub(' ', text)
    text = text.lower().strip()
    return re.sub(r'\s+', ' ', text)


def _clean_tag(text):
    """Remove leading [tag] prefix from a task name."""
    if not text:
        return ''
    m = _TAG_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


def _jaccard(a, b):
    """Jaccard word-overlap similarity in [0.0, 1.0]."""
    tokens_a = set(_normalize(a).split())
    tokens_b = set(_normalize(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


class DocProjectEnricher(models.AbstractModel):
    """
    Enriches doc.module / doc.menu / doc.function with text from task snapshots.

    All data is read from doc.project.task.snapshot — no live project.task
    queries.  Call import_from_project() first to populate the snapshot table.
    """

    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_module(self, doc_module, overwrite=False, project_id=False):
        """
        Enrich a doc.module using its generation's task snapshots.

        Parameters
        ----------
        doc_module  : doc.module (single record)
        overwrite   : if True overwrite existing non-empty fields
        project_id  : kept for backward-compat / logging only (not used for queries)

        Returns
        -------
        dict: module_enriched, menus_enriched, functions_enriched, skipped
        """
        stats = {
            'module_enriched': False,
            'menus_enriched': 0,
            'functions_enriched': 0,
            'skipped': 0,
        }

        generation_id = doc_module.generation_id.id if doc_module.generation_id else False
        if not generation_id:
            _logger.warning(
                'enrich_module: doc_module id=%s has no generation_id — '
                'cannot locate snapshots',
                doc_module.id,
            )
            return stats

        technical_name = doc_module.technical_name or ''
        display_name = doc_module.name or ''

        # Load all snapshots for this generation run (single query)
        all_snaps = self.env['doc.project.task.snapshot'].search(
            [('generation_id', '=', generation_id)],
            order='depth asc, sequence asc, id asc',
        )

        if not all_snaps:
            _logger.warning(
                'enrich_module: no snapshots found for generation_id=%s. '
                'Click "Re-import from Project" first.',
                generation_id,
            )
            return stats

        _logger.info(
            'enrich_module: module=%s generation_id=%s total_snapshots=%s',
            technical_name, generation_id, len(all_snaps),
        )

        # Step 1: find module-parent snapshots tagged [technical_name]
        module_parent_snaps = self._find_module_parent_snaps(
            technical_name, display_name, all_snaps
        )

        if module_parent_snaps:
            # Collect functional (child) snapshots for matching menus/functions
            Snap = self.env['doc.project.task.snapshot']
            functional_snaps = Snap.browse()
            for parent in module_parent_snaps:
                children = parent.child_snapshot_ids.filtered(
                    lambda s: s.description_plain.strip() or s.name.strip()
                )
                functional_snaps |= children

            if not functional_snaps:
                # No children — use the parent snapshots themselves
                functional_snaps = Snap.browse([s.id for s in module_parent_snaps])

            stats['module_enriched'] = self._enrich_module_description(
                doc_module, module_parent_snaps[0], overwrite=overwrite
            )
            _logger.info(
                '[ENRICH TAG] module=%s parent_snaps=%s functional_snaps=%s',
                technical_name, len(module_parent_snaps), len(functional_snaps),
            )
        else:
            # Fallback: fuzzy keyword search across all snapshots
            functional_snaps = self._find_functional_snaps_fuzzy(
                technical_name, display_name, all_snaps
            )
            if functional_snaps:
                stats['module_enriched'] = self._enrich_module_description(
                    doc_module, functional_snaps[0], overwrite=overwrite
                )
            _logger.info(
                '[ENRICH FUZZY] module=%s functional_snaps=%s',
                technical_name, len(functional_snaps),
            )

        if not functional_snaps:
            _logger.warning(
                '[ENRICH] No functional snapshots for module=%s (generation=%s). '
                'Make sure tasks are prefixed [%s] in the project, then '
                'click "Re-import from Project".',
                technical_name, generation_id, technical_name,
            )
            return stats

        _logger.info(
            '[ENRICH] Using %s functional snaps for module=%s: %s',
            len(functional_snaps), technical_name,
            [s.name for s in list(functional_snaps)[:10]],
        )

        for menu in doc_module.menu_ids:
            if self._enrich_menu_caption(menu, functional_snaps, overwrite=overwrite):
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        for func in doc_module.function_ids:
            if self._enrich_function(func, functional_snaps, overwrite=overwrite):
                stats['functions_enriched'] += 1

        _logger.info(
            '[ENRICH] DONE module=%s: enriched=%s menus=%s funcs=%s skipped=%s',
            technical_name, stats['module_enriched'],
            stats['menus_enriched'], stats['functions_enriched'], stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------
    # Snapshot discovery helpers
    # ------------------------------------------------------------------

    def _find_module_parent_snaps(self, technical_name, display_name, all_snaps):
        """
        Find snapshots that represent the module-parent task.

        Priority
        --------
        1. Exact module_tag match:  snap.module_tag == technical_name
           This correctly resolves:
             technical_name = 'dpf_events'
             task name      = '[dpf_events] Создание мероприятий ...'
             module_tag     = 'dpf_events'  (stored at import time)  → MATCH

        2. Display name Jaccard >= 0.4 against snap.name
           Handles cases where task names contain the human display name.
        """
        result = []

        # Priority 1: exact module_tag match (pre-parsed at import time)
        for snap in all_snaps:
            tag = (snap.module_tag or '').lower()
            if tag and tag == technical_name.lower():
                result.append(snap)
                _logger.info(
                    '_find_module_parent_snaps: TAG MATCH id=%s name=%s tag=%s',
                    snap.id, snap.name, tag,
                )

        if result:
            # Prefer snapshots that have the most children (richer data)
            result.sort(key=lambda s: -len(s.child_snapshot_ids))
            return result

        # Priority 2: display name Jaccard similarity
        if display_name:
            for snap in all_snaps:
                score = _jaccard(display_name, snap.name)
                if score >= 0.4:
                    result.append(snap)
                    _logger.info(
                        '_find_module_parent_snaps: DISPLAY_NAME MATCH '
                        'id=%s name=%s score=%.2f',
                        snap.id, snap.name, score,
                    )

        if result:
            result.sort(key=lambda s: -len(s.child_snapshot_ids))
            return result

        _logger.info(
            '_find_module_parent_snaps: nothing found for '
            'technical_name=%s display_name=%s',
            technical_name, display_name,
        )
        return []

    def _find_functional_snaps_fuzzy(self, technical_name, display_name, all_snaps):
        """Fallback: collect snapshots whose name contains a keyword from technical_name."""
        keywords = {
            k.lower() for k in [
                technical_name,
                technical_name.replace('_', ' '),
                display_name,
            ] if k
        }
        result = []
        seen_ids = set()
        for snap in all_snaps:
            name_lower = (snap.name or '').lower()
            if any(kw in name_lower for kw in keywords):
                if snap.id not in seen_ids:
                    seen_ids.add(snap.id)
                    result.append(snap)
        # Return as a recordset for consistent API
        Snap = self.env['doc.project.task.snapshot']
        return Snap.browse([s.id for s in result])

    # ------------------------------------------------------------------
    # Matching utility
    # ------------------------------------------------------------------

    def _best_snap_match(self, name, snaps, threshold=0.30, extra_names=None):
        """
        Find the best matching snapshot for a given name using Jaccard similarity.

        Parameters
        ----------
        name         : str   — function or menu name to match
        snaps        : recordset of doc.project.task.snapshot
        threshold    : float — minimum score to accept
        extra_names  : list  — additional name variants (e.g. parent menu names)

        Returns
        -------
        (snapshot | None, float)
        """
        best_snap = None
        best_score = 0.0

        names_to_try = [(name or '', threshold)]
        for extra in (extra_names or []):
            if extra and extra.strip():
                # Extra names use a slightly lower threshold
                names_to_try.append((extra.strip(), max(threshold - 0.10, 0.15)))

        for snap in snaps:
            raw_name = snap.name or ''
            clean_name = snap.name_clean or _clean_tag(raw_name)

            for query, thr in names_to_try:
                score = max(
                    _jaccard(raw_name, query),
                    _jaccard(clean_name, query),
                )
                if score > best_score:
                    best_score = score
                    best_snap = snap

        effective_threshold = min(t for _, t in names_to_try)

        if best_snap:
            _logger.info(
                '_best_snap_match: query="%s" -> snap="%s" score=%.2f '
                '(threshold=%.2f) match=%s',
                name, best_snap.name, best_score,
                effective_threshold, best_score >= effective_threshold,
            )
        return (best_snap, best_score) if best_score >= effective_threshold else (None, 0.0)

    # ------------------------------------------------------------------
    # Enrichment write methods
    # ------------------------------------------------------------------

    def _enrich_module_description(self, doc_module, parent_snap, overwrite=False):
        """Write module description from the parent snapshot."""
        if doc_module.description and not overwrite:
            return False
        raw_desc = (parent_snap.description_plain or '').strip()
        if not raw_desc:
            raw_desc = parent_snap.name_clean or parent_snap.name or ''
        if not raw_desc:
            return False
        doc_module.write({
            'description': raw_desc,
            'project_context_source': parent_snap.name,
            'project_context_note': _(
                'Description imported from project task snapshot: "%s" '
                '(original_task_id=%s)'
            ) % (parent_snap.name, parent_snap.original_task_id),
            'project_task_name_snapshot': parent_snap.name,
        })
        return True

    def _enrich_menu_caption(self, menu, snaps, overwrite=False, threshold=0.35):
        """Write menu caption from the best matching snapshot."""
        if not overwrite:
            src = menu.caption_source or 'generated'
            if src == 'manual':
                return False
            if src == 'task' and menu.caption:
                return False

        best_snap, _score = self._best_snap_match(
            menu.name or '', snaps, threshold=threshold
        )
        if not best_snap:
            # Try the last segment of the full menu path
            last_segment = (menu.complete_name or '').split('/')[-1].strip()
            best_snap, _score = self._best_snap_match(
                last_segment, snaps, threshold=threshold
            )
        if not best_snap:
            return False

        caption_text = (best_snap.description_plain or '').strip()
        if not caption_text:
            caption_text = best_snap.name_clean or best_snap.name or ''
        if not caption_text:
            return False

        menu.write({
            'caption': caption_text,
            'caption_source': 'task',
            'caption_task_name_snapshot': best_snap.name,
        })
        return True

    def _enrich_function(self, func, snaps, overwrite=False, threshold=0.30):
        """
        Enrich a doc.function from the best matching snapshot.

        For auto-generated functions (e.g. 'Create Record of X') the name may
        not overlap well with Russian task names, so we also pass the linked
        menu hierarchy names as extra hints to the matcher.
        """
        func_name = func.name or ''

        # Build extra name hints from the linked menu chain
        extra_names = []
        menu = getattr(func, 'menu_id', None)
        if menu:
            if menu.name:
                extra_names.append(menu.name)
            complete = getattr(menu, 'complete_name', '') or ''
            for segment in complete.split('/'):
                segment = segment.strip()
                if segment and segment not in extra_names:
                    extra_names.append(segment)

        # For 'Create Record' type functions add the verb as extra signal
        if _CREATE_RE.search(func_name):
            extra_names.append('\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0435')  # 'создание'

        best_snap, _score = self._best_snap_match(
            func_name, snaps, threshold=threshold, extra_names=extra_names or None
        )
        if not best_snap:
            return False

        raw_text = (best_snap.description_plain or '').strip()
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

    # ------------------------------------------------------------------
    # Text parsing utility
    # ------------------------------------------------------------------

    def _parse_subtask_sections(self, plain_text):
        """
        Split plain-text description into structured sections.

        Recognizes Russian section headers:
            Описание / Требования / Порядок / Результат
        If no headers are found the entire text goes into 'description'.
        """
        result = {'description': '', 'requirements': '', 'steps': '', 'result': ''}
        if not plain_text:
            return result

        _KEY_MAP = {
            '\u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435': 'description',
            '\u0442\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f': 'requirements',
            '\u043f\u043e\u0440\u044f\u0434\u043e\u043a': 'steps',
            '\u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442': 'result',
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
