# -*- coding: utf-8 -*-
"""
Project task enricher — reads from doc.project.task.snapshot.

Key fixes vs original
---------------------
1.  Name-matching was broken because:
      * project_task_project_id defaulted to 0 (falsy Integer).
      * Even with correct project_id, Russian task names scored Jaccard=0
        against English menu/function names — nothing ever matched.

2.  New strategy: enricher NO LONGER tries to match tasks to existing
    functions by name similarity.  Instead:
      a. Find all snapshots tagged [technical_name]  (depth 1 nodes).
      b. Collect their children (depth 2 — the actual functional subtasks).
      c. For each child create or update a doc.function directly.
         The task's clean name becomes the function title,
         the task's description sections fill description/requirements/steps/result.
    This works regardless of language, because we match on [tag] prefix only.

3.  Menu enrichment still uses Jaccard but with a much lower threshold (0.15)
    as a best-effort extra.

4.  Snapshots can now come from a global doc.project.snapshot.set
    (preferred) or fall back to per-generation snapshots.
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[([\w]+)\]\s*', flags=re.UNICODE)
_NOISE_RE = re.compile(
    r'ТЗ|ТС|§[\d\.]+|§\d|\[.*?\]|["«»()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)
_SECTION_RE = re.compile(
    r'^\s*(?P<key>Описание|Требования|Порядок|Результат)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)


def _normalize(text):
    """Lowercase, NFC-normalize, strip noise chars, collapse whitespace."""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    text = _NOISE_RE.sub(' ', text)
    text = text.lower().strip()
    return re.sub(r'\s+', ' ', text)


def _clean_tag(text):
    """Remove leading [tag] prefix."""
    if not text:
        return ''
    m = _TAG_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


def _jaccard(a, b):
    """Jaccard word-overlap similarity in [0.0, 1.0]."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class DocProjectEnricher(models.AbstractModel):
    """
    Enriches doc.module / doc.menu / doc.function with task snapshot data.
    """

    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_module(self, doc_module, overwrite=False, project_id=False):
        """
        Enrich a single doc.module from task snapshots.

        Sources searched (in priority order):
          1. Global snapshot set linked on the generation (snapshot_set_id).
          2. Per-generation snapshots (legacy / fallback).

        Parameters
        ----------
        doc_module  : doc.module record
        overwrite   : bool — overwrite existing non-empty fields
        project_id  : int  — kept for backward compat / logging only

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

        generation = doc_module.generation_id
        if not generation:
            _logger.warning(
                'enrich_module: doc_module id=%s has no generation_id', doc_module.id
            )
            return stats

        technical_name = (doc_module.technical_name or '').lower()
        display_name = doc_module.name or ''

        all_snaps = self._load_snaps_for_generation(generation)

        if not all_snaps:
            _logger.warning(
                'enrich_module: no snapshots for generation_id=%s.\n'
                'Tip: either select a Global Snapshot Set on the generation,\n'
                'or click "Re-import from Project" to download per-generation snaps.',
                generation.id,
            )
            return stats

        _logger.info(
            'enrich_module: module=%s generation=%s total_snaps=%s',
            technical_name, generation.id, len(all_snaps),
        )

        # Step 1 — find depth-1 snapshots tagged [technical_name]
        parent_snaps = self._find_module_parent_snaps(
            technical_name, display_name, all_snaps
        )

        if not parent_snaps:
            _logger.warning(
                'enrich_module: no parent snaps found for [%s]. '
                'Make sure tasks in the project are prefixed [%s].',
                technical_name, technical_name,
            )
            return stats

        _logger.info(
            'enrich_module: found %s parent snap(s) for [%s]: %s',
            len(parent_snaps), technical_name,
            [s.name for s in parent_snaps],
        )

        # Step 2 — collect functional (depth-2) child snapshots
        Snap = self.env['doc.project.task.snapshot']
        functional_snaps = Snap.browse()
        for parent in parent_snaps:
            functional_snaps |= parent.child_snapshot_ids

        if not functional_snaps:
            # No children — parent IS the functional snap
            functional_snaps = Snap.browse([s.id for s in parent_snaps])

        _logger.info(
            'enrich_module: %s functional snaps for [%s]: %s',
            len(functional_snaps), technical_name,
            [s.name for s in list(functional_snaps)[:15]],
        )

        # Step 3 — enrich module description from first parent
        stats['module_enriched'] = self._enrich_module_description(
            doc_module, parent_snaps[0], overwrite=overwrite
        )

        # Step 4 — create / update doc.function records from ALL functional snaps
        stats['functions_enriched'] = self._upsert_functions_from_snaps(
            doc_module, functional_snaps, overwrite=overwrite
        )

        # Step 5 — best-effort menu caption enrichment (Jaccard, low threshold)
        for menu in doc_module.menu_ids:
            if self._enrich_menu_caption(menu, functional_snaps, overwrite=overwrite):
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        _logger.info(
            'enrich_module DONE: module=%s enriched=%s menus=%s funcs=%s skipped=%s',
            technical_name,
            stats['module_enriched'],
            stats['menus_enriched'],
            stats['functions_enriched'],
            stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------
    # Snapshot loading
    # ------------------------------------------------------------------

    def _load_snaps_for_generation(self, generation):
        """
        Load snapshots preferring a linked global snapshot set.

        Priority:
          1. generation.snapshot_set_id  (global set)
          2. per-generation snapshots    (legacy)
        """
        Snap = self.env['doc.project.task.snapshot']
        snapshot_set = getattr(generation, 'snapshot_set_id', None)
        if snapshot_set and snapshot_set.id:
            snaps = Snap.search(
                [('snapshot_set_id', '=', snapshot_set.id)],
                order='depth asc, sequence asc, id asc',
            )
            if snaps:
                _logger.info(
                    '_load_snaps_for_generation: using global set id=%s (%s snaps)',
                    snapshot_set.id, len(snaps),
                )
                return snaps

        # Fall back to per-generation snapshots
        snaps = Snap.search(
            [('generation_id', '=', generation.id)],
            order='depth asc, sequence asc, id asc',
        )
        _logger.info(
            '_load_snaps_for_generation: using per-gen snaps for gen=%s (%s snaps)',
            generation.id, len(snaps),
        )
        return snaps

    # ------------------------------------------------------------------
    # Snapshot discovery
    # ------------------------------------------------------------------

    def _find_module_parent_snaps(self, technical_name, display_name, all_snaps):
        """
        Find snapshots that are the module-parent task.

        Matching priority:
          1. Exact module_tag match: snap.module_tag == technical_name
             Resolves:  technical_name='dpf_events'
                        task name='[dpf_events] Создание мероприятий'
                        → module_tag='dpf_events' → ✓
          2. Display name Jaccard ≥ 0.35 (secondary fuzzy fallback)
        """
        result = []

        for snap in all_snaps:
            tag = (snap.module_tag or '').lower()
            if tag and tag == technical_name.lower():
                result.append(snap)

        if result:
            _logger.info(
                '_find_module_parent_snaps: TAG MATCH — %s snaps for [%s]',
                len(result), technical_name,
            )
            # Sort: snaps with children first (they are the real parent tasks)
            result.sort(key=lambda s: -len(s.child_snapshot_ids))
            return result

        # Fallback: display name similarity
        if display_name:
            for snap in all_snaps:
                score = _jaccard(display_name, snap.name)
                if score >= 0.35:
                    result.append(snap)
            if result:
                result.sort(key=lambda s: -len(s.child_snapshot_ids))
                _logger.info(
                    '_find_module_parent_snaps: DISPLAY_NAME fallback — '
                    '%s snaps for display_name=%s',
                    len(result), display_name,
                )
                return result

        return []

    # ------------------------------------------------------------------
    # Core enrichment: create/update doc.function from snapshots
    # ------------------------------------------------------------------

    def _upsert_functions_from_snaps(self, doc_module, functional_snaps, overwrite=False):
        """
        Create or update doc.function records from functional snapshot tasks.

        Strategy (language-agnostic — no Jaccard matching needed):
          * For each functional snapshot, check if a doc.function with the
            same original_task_id already exists on this doc_module.
          * If yes  → update its fields (if overwrite or fields are empty).
          * If no   → create a new doc.function record.

        Returns the number of functions created or updated.
        """
        if not functional_snaps:
            return 0

        # Build index of existing functions by their source snap task id
        existing = {}
        for func in doc_module.function_ids:
            key = getattr(func, 'source_task_id', 0) or 0
            if key:
                existing[key] = func

        count = 0
        for idx, snap in enumerate(functional_snaps, start=1):
            sections = self._parse_subtask_sections(
                (snap.description_plain or '').strip()
            )

            # Build title: prefer clean name, fall back to raw name
            title = (snap.name_clean or _clean_tag(snap.name) or snap.name or '').strip()
            if not title:
                continue

            # Derive description: use structured sections or full text
            desc = sections.get('description', '').strip()
            reqs = sections.get('requirements', '').strip()
            steps = sections.get('steps', '').strip()
            result_text = sections.get('result', '').strip()

            # If no structured sections, put everything into description
            if not any([desc, reqs, steps, result_text]):
                desc = (snap.description_plain or '').strip()

            task_id = snap.original_task_id or 0
            func = existing.get(task_id)

            if func:
                # Update existing
                updates = {}
                if (not func.description or overwrite) and desc:
                    updates['description'] = desc
                if (not func.requirements or overwrite) and reqs:
                    updates['requirements'] = reqs
                if (not func.steps or overwrite) and steps:
                    updates['steps'] = steps
                if (not func.result or overwrite) and result_text:
                    updates['result'] = result_text
                if updates:
                    func.write(updates)
                    count += 1
            else:
                # Determine sequence: after existing auto functions
                seq = (len(doc_module.function_ids) + idx) * 10
                vals = {
                    'doc_module_id': doc_module.id,
                    'name': title,
                    'sequence': seq,
                    'description': desc or False,
                    'requirements': reqs or False,
                    'steps': steps or False,
                    'result': result_text or False,
                }
                # Store source task id for future upsert matching
                # (field added below; safe to ignore if field not present yet)
                if 'source_task_id' in self.env['doc.function']._fields:
                    vals['source_task_id'] = task_id or False
                self.env['doc.function'].create(vals)
                count += 1

        _logger.info(
            '_upsert_functions_from_snaps: module=%s created/updated %s functions',
            doc_module.technical_name, count,
        )
        return count

    # ------------------------------------------------------------------
    # Menu caption enrichment (best-effort, Jaccard)
    # ------------------------------------------------------------------

    def _enrich_menu_caption(self, menu, snaps, overwrite=False, threshold=0.15):
        """Write menu caption from the best matching snapshot (low threshold)."""
        if not overwrite:
            src = getattr(menu, 'caption_source', 'generated') or 'generated'
            if src == 'manual':
                return False
            if src == 'task' and menu.caption:
                return False

        best_snap, _score = self._best_snap_match(
            menu.name or '', snaps, threshold=threshold
        )
        if not best_snap:
            last_seg = (menu.complete_name or '').split('/')[-1].strip()
            best_snap, _score = self._best_snap_match(
                last_seg, snaps, threshold=threshold
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

    def _best_snap_match(self, name, snaps, threshold=0.15, extra_names=None):
        """Find best matching snapshot by Jaccard similarity."""
        best_snap = None
        best_score = 0.0

        names_to_try = [(name or '', threshold)]
        for extra in (extra_names or []):
            if extra and extra.strip():
                names_to_try.append((extra.strip(), max(threshold - 0.05, 0.10)))

        for snap in snaps:
            raw_name = snap.name or ''
            clean_name = snap.name_clean or _clean_tag(raw_name)
            for query, thr in names_to_try:
                score = max(_jaccard(raw_name, query), _jaccard(clean_name, query))
                if score > best_score:
                    best_score = score
                    best_snap = snap

        effective_threshold = min(t for _, t in names_to_try)
        if best_snap and best_score >= effective_threshold:
            _logger.info(
                '_best_snap_match: "%s" → "%s" score=%.2f',
                name, best_snap.name, best_score,
            )
            return best_snap, best_score
        return None, 0.0

    # ------------------------------------------------------------------
    # Module description
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
        doc_module.write({'description': raw_desc})
        return True

    # ------------------------------------------------------------------
    # Section parser
    # ------------------------------------------------------------------

    def _parse_subtask_sections(self, plain_text):
        """
        Split plain text into structured sections by Russian headers.

        Recognised headers: Описание / Требования / Порядок / Результат
        If none found, entire text goes into 'description'.
        """
        result = {'description': '', 'requirements': '', 'steps': '', 'result': ''}
        if not plain_text:
            return result

        _KEY_MAP = {
            'описание': 'description',
            'требования': 'requirements',
            'порядок': 'steps',
            'результат': 'result',
        }
        current_key = 'description'
        buckets = {k: [] for k in result}

        for line in plain_text.splitlines():
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
