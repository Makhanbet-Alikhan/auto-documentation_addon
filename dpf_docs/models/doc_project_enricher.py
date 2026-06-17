# -*- coding: utf-8 -*-
"""
Project task enricher — reads from doc.project.task.snapshot.

How it works
------------
1. The user OPTIONALLY selects a Global Snapshot Set on the doc.generation form.
   If no snapshot set is selected, enrichment is completely skipped (no errors,
   no warnings, the documentation is generated without task data).

2. Snapshots are loaded from the selected set (global) or from per-generation
   snapshots (legacy fallback).

3. For the module being documented (e.g. technical_name='dpf_docs') the enricher
   searches for snapshot tasks whose [tag] prefix matches the technical name:
     Task name: '[dpf_docs] Создание документации'  →  module_tag='dpf_docs'  ✓
   If no tasks are tagged for this module, nothing is written — the module's
   fields are left as-is and generation continues normally.

4. When matching tasks ARE found:
   a. Module description is filled from the parent task's description.
   b. Child tasks (subtasks) → upserted as doc.function records.
      Each subtask description is parsed for structured Russian sections:
        Описание / Требования / Порядок / Результат
   c. Menu captions receive a best-effort fill from Jaccard similarity
      (low threshold 0.15 — pure bonus, no hard requirement).

All matching is language-agnostic at the tag level — only the [tag] prefix
matters, task bodies can be in any language.
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[[\w]+\]\s*', flags=re.UNICODE)
_NOISE_RE = re.compile(
    r'ТЗ|ТС|§[\d\.]+|§\d|\[.*?\]|["«»()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)
_SECTION_RE = re.compile(
    r'^\s*(?P<key>Описание|Требования|Порядок|Результат)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class DocProjectEnricher(models.AbstractModel):
    """
    Enriches doc.module / doc.menu / doc.function records with project task data.

    Usage
    -----
    Call  self.env['doc.project.enricher'].enrich_module(doc_module)
    from doc_generation.py after the basic doc spec is built.

    The method is fully safe to call even when:
      - No snapshot set is linked (returns empty stats, does nothing).
      - No tasks match the module's technical name (same).
    """

    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def enrich_module(self, doc_module, overwrite=False):
        """
        Enrich a single doc.module from project task snapshots.

        This method is OPTIONAL — if no snapshot set is configured on the
        generation, it returns immediately with empty stats and logs a single
        info message.  It never raises.

        Parameters
        ----------
        doc_module  : doc.module record (browse)
        overwrite   : bool — if True, existing non-empty fields are overwritten;
                      if False (default), only empty fields are filled.

        Returns
        -------
        dict with keys:
          module_enriched   (bool)
          menus_enriched    (int)
          functions_enriched (int)
          skipped           (int)
          reason            (str)  — short explanation of what happened
        """
        stats = {
            'module_enriched': False,
            'menus_enriched': 0,
            'functions_enriched': 0,
            'skipped': 0,
            'reason': 'not_started',
        }

        # ---- Step 0: validate generation linkage ----
        generation = doc_module.generation_id
        if not generation:
            stats['reason'] = 'no_generation'
            _logger.debug(
                'enrich_module: doc_module id=%s has no generation_id — skip',
                doc_module.id,
            )
            return stats

        technical_name = (doc_module.technical_name or '').lower().strip()
        if not technical_name:
            stats['reason'] = 'no_technical_name'
            _logger.debug('enrich_module: doc_module id=%s has no technical_name — skip', doc_module.id)
            return stats

        # ---- Step 1: load snapshots (OPTIONAL) ----
        all_snaps = self._load_snaps_for_generation(generation)

        if not all_snaps:
            # No snapshots configured — this is perfectly normal.
            stats['reason'] = 'no_snapshots_configured'
            _logger.info(
                'enrich_module: module=%s — no snapshot set configured on '
                'generation id=%s.  Enrichment skipped (documentation will be '
                'generated without project task data). '
                'To enable: select a Global Snapshot Set on the generation form.',
                technical_name, generation.id,
            )
            return stats

        _logger.info(
            'enrich_module: module=%s  generation=%s  total_snaps=%s',
            technical_name, generation.id, len(all_snaps),
        )

        # ---- Step 2: find tasks tagged [technical_name] ----
        parent_snaps = self._find_module_parent_snaps(technical_name, all_snaps)

        if not parent_snaps:
            # No tasks for this module — normal, not an error.
            stats['reason'] = 'no_matching_tasks'
            _logger.info(
                'enrich_module: module=%s — no tasks found with tag [%s] '
                'in the snapshot set.  Enrichment skipped for this module.  '
                'If you expect task data, make sure project tasks are named '
                'like "[%s] Task description".',
                technical_name, technical_name, technical_name,
            )
            return stats

        _logger.info(
            'enrich_module: found %s parent snap(s) for [%s]: %s',
            len(parent_snaps), technical_name,
            [s.name for s in parent_snaps],
        )

        # ---- Step 3: collect functional (child) snapshots ----
        # Children are the actual feature/function subtasks.
        Snap = self.env['doc.project.task.snapshot']
        functional_snaps = Snap.browse()
        for parent in parent_snaps:
            functional_snaps |= parent.child_snapshot_ids

        # If no children found, treat the parent tasks themselves as functional
        if not functional_snaps:
            functional_snaps = Snap.browse([s.id for s in parent_snaps])

        _logger.info(
            'enrich_module: %s functional snap(s) for [%s]',
            len(functional_snaps), technical_name,
        )

        # ---- Step 4: fill module description ----
        stats['module_enriched'] = self._enrich_module_description(
            doc_module, parent_snaps[0], overwrite=overwrite
        )

        # ---- Step 5: upsert doc.function records from subtasks ----
        stats['functions_enriched'] = self._upsert_functions_from_snaps(
            doc_module, functional_snaps, overwrite=overwrite
        )

        # ---- Step 6: best-effort menu caption enrichment (Jaccard) ----
        for menu in doc_module.menu_ids:
            if self._enrich_menu_caption(menu, functional_snaps, overwrite=overwrite):
                stats['menus_enriched'] += 1
            else:
                stats['skipped'] += 1

        stats['reason'] = 'enriched'
        _logger.info(
            'enrich_module DONE: module=%s  enriched=%s  menus=%s  funcs=%s  skipped=%s',
            technical_name,
            stats['module_enriched'],
            stats['menus_enriched'],
            stats['functions_enriched'],
            stats['skipped'],
        )
        return stats

    # ------------------------------------------------------------------ #
    # Snapshot loading                                                     #
    # ------------------------------------------------------------------ #

    def _load_snaps_for_generation(self, generation):
        """
        Load snapshots for this generation run.

        Priority:
          1. generation.snapshot_set_id  — global set (preferred)
          2. per-generation snapshots    — legacy fallback

        Returns an empty recordset if neither source has data.
        This is NOT an error — enrichment simply won't run.
        """
        Snap = self.env['doc.project.task.snapshot']

        # Try global snapshot set first
        snapshot_set = getattr(generation, 'snapshot_set_id', None)
        if snapshot_set and snapshot_set.id:
            snaps = Snap.search(
                [('snapshot_set_id', '=', snapshot_set.id)],
                order='depth asc, sequence asc, id asc',
            )
            if snaps:
                _logger.info(
                    '_load_snaps: global set id=%s  %s snaps',
                    snapshot_set.id, len(snaps),
                )
                return snaps

        # Fall back to per-generation snapshots (legacy)
        snaps = Snap.search(
            [('generation_id', '=', generation.id)],
            order='depth asc, sequence asc, id asc',
        )
        if snaps:
            _logger.info(
                '_load_snaps: legacy per-gen snaps  gen=%s  %s snaps',
                generation.id, len(snaps),
            )
        return snaps

    # ------------------------------------------------------------------ #
    # Task → module matching                                               #
    # ------------------------------------------------------------------ #

    def _find_module_parent_snaps(self, technical_name, all_snaps):
        """
        Find snapshots that are the parent tasks for this module.

        Matching strategy (in priority order):
          1. Exact tag match: snap.module_tag == technical_name
             Example:  technical_name='dpf_docs'
                       task='[dpf_docs] Документация'  →  module_tag='dpf_docs'  ✓
          2. No fallback fuzzy match — if no tagged tasks exist for this module,
             we return [] and enrichment is skipped cleanly.
             (Fuzzy matching by name was removed because Russian task names
              always scored Jaccard=0 against English technical module names.)

        Returns sorted list: snaps with most children first (likely the real
        parent tasks, not leaf tasks that happen to have the same tag).
        """
        result = [
            snap for snap in all_snaps
            if (snap.module_tag or '').lower() == technical_name
        ]

        if result:
            # Sort: most children first — these are the true parent tasks
            result.sort(key=lambda s: -len(s.child_snapshot_ids))
            _logger.info(
                '_find_module_parent_snaps: [%s] → %s tagged snap(s)',
                technical_name, len(result),
            )
        return result

    # ------------------------------------------------------------------ #
    # doc.function upsert from subtask snapshots                          #
    # ------------------------------------------------------------------ #

    def _upsert_functions_from_snaps(self, doc_module, functional_snaps, overwrite=False):
        """
        Create or update doc.function records from functional (subtask) snapshots.

        Matching key: snap.original_task_id  ↔  func.source_task_id
          * If a doc.function with source_task_id == snap.original_task_id already
            exists on this module → update its fields (respecting overwrite flag).
          * Otherwise → create a new doc.function.

        Each subtask description is parsed for structured Russian section headers:
          Описание / Требования / Порядок / Результат
        If none found, the full description text goes into 'description'.

        Returns the count of functions created or updated.
        """
        if not functional_snaps:
            return 0

        # Build index of existing functions keyed by their source task id
        existing_by_task_id = {}
        for func in doc_module.function_ids:
            key = getattr(func, 'source_task_id', 0) or 0
            if key:
                existing_by_task_id[key] = func

        count = 0
        for idx, snap in enumerate(functional_snaps, start=1):
            # Parse structured sections from description
            sections = self._parse_subtask_sections(
                (snap.description_plain or '').strip()
            )

            # Build display title
            title = (snap.name_clean or _clean_tag(snap.name) or snap.name or '').strip()
            if not title:
                continue

            desc = sections.get('description', '').strip()
            reqs = sections.get('requirements', '').strip()
            steps = sections.get('steps', '').strip()
            result_text = sections.get('result', '').strip()

            # No structured sections → dump full text into description
            if not any([desc, reqs, steps, result_text]):
                desc = (snap.description_plain or '').strip()

            task_id = snap.original_task_id or 0
            func = existing_by_task_id.get(task_id)

            if func:
                # Update existing function
                updates = {}
                if (overwrite or not func.description) and desc:
                    updates['description'] = desc
                if (overwrite or not getattr(func, 'requirements', None)) and reqs:
                    updates['requirements'] = reqs
                if (overwrite or not getattr(func, 'steps', None)) and steps:
                    updates['steps'] = steps
                if (overwrite or not getattr(func, 'result', None)) and result_text:
                    updates['result'] = result_text
                if updates:
                    func.write(updates)
                    count += 1
            else:
                # Create new function
                seq = (len(doc_module.function_ids) + idx) * 10
                vals = {
                    'doc_module_id': doc_module.id,
                    'name': title,
                    'sequence': seq,
                    'description': desc or False,
                }
                # Optional structured fields — only write if the field exists
                for field_name, value in [
                    ('requirements', reqs or False),
                    ('steps', steps or False),
                    ('result', result_text or False),
                    ('source_task_id', task_id or False),
                ]:
                    if field_name in self.env['doc.function']._fields:
                        vals[field_name] = value

                self.env['doc.function'].create(vals)
                count += 1

        _logger.info(
            '_upsert_functions_from_snaps: module=%s  created/updated %s functions',
            doc_module.technical_name, count,
        )
        return count

    # ------------------------------------------------------------------ #
    # Menu caption enrichment (best-effort)                                #
    # ------------------------------------------------------------------ #

    def _enrich_menu_caption(self, menu, snaps, overwrite=False, threshold=0.15):
        """
        Fill menu caption from the best-matching snapshot (Jaccard, low threshold).

        This is a best-effort bonus — never required for successful documentation.
        Returns True if caption was written, False otherwise.
        """
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
            # Try matching against the last segment of complete_name
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

    def _best_snap_match(self, name, snaps, threshold=0.15):
        """Find best Jaccard match for name among snaps."""
        best_snap = None
        best_score = 0.0
        for snap in snaps:
            raw = snap.name or ''
            clean = snap.name_clean or _clean_tag(raw)
            score = max(_jaccard(raw, name), _jaccard(clean, name))
            if score > best_score:
                best_score = score
                best_snap = snap
        if best_snap and best_score >= threshold:
            return best_snap, best_score
        return None, 0.0

    # ------------------------------------------------------------------ #
    # Module description                                                   #
    # ------------------------------------------------------------------ #

    def _enrich_module_description(self, doc_module, parent_snap, overwrite=False):
        """Write module description from the parent snapshot text."""
        if doc_module.description and not overwrite:
            return False
        raw_desc = (parent_snap.description_plain or '').strip()
        if not raw_desc:
            raw_desc = parent_snap.name_clean or parent_snap.name or ''
        if not raw_desc:
            return False
        doc_module.write({'description': raw_desc})
        return True

    # ------------------------------------------------------------------ #
    # Section parser                                                       #
    # ------------------------------------------------------------------ #

    def _parse_subtask_sections(self, plain_text):
        """
        Split plain text into structured documentation sections.

        Recognised Russian section headers (case-insensitive):
          Описание   → 'description'
          Требования → 'requirements'
          Порядок    → 'steps'
          Результат  → 'result'

        If no headers are found, the entire text is placed in 'description'.
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

        # If no structured sections found — put everything in description
        if not any([result['requirements'], result['steps'], result['result']]):
            result['description'] = plain_text.strip()

        return result
