# -*- coding: utf-8 -*-
"""
Project task enricher — reads from doc.project.task.snapshot.

How it works
------------
1. The user OPTIONALLY selects a Global Snapshot Set on the doc.generation form.
   If no snapshot set is selected, enrichment is completely skipped.

2. For the module being documented (e.g. technical_name='dpf_events') the enricher
   searches for snapshot tasks whose [tag] prefix matches the technical name:
     Task name: '[dpf_events] Rooms'  ->  module_tag='dpf_events'  OK
   If no tasks are tagged for this module, nothing is written.

3. When matching tasks ARE found:
   a. Module description is filled from the parent task's description.
   b. Child tasks (subtasks) -> matched against EXISTING doc.function records
      by name similarity (Jaccard).  If a good match is found the existing
      function is ENRICHED IN-PLACE (description / steps / result fields
      are filled without changing sequence or position).  If no match is found
      a new doc.function is created at the very end.
   c. Menu captions receive a best-effort fill from Jaccard similarity.
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[[\w]+\]\s*', flags=re.UNICODE)
_NOISE_RE = re.compile(
    r'\u0422\u0417|\u0422\u0421|\xa7[\d\.]+|\xa7\d|\[.*?\]|["\u00ab\u00bb()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)
_SECTION_RE = re.compile(
    r'^\s*(?P<key>\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435|\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f|\u041f\u043e\u0440\u044f\u0434\u043e\u043a|\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)

# Threshold for matching a task name to an existing function name.
# 0.25 means at least 25% word overlap — enough to match
# "[dpf_events] Rooms" with function "Просмотр списка комнат" when
# both have a shared keyword, but not so low as to cause false positives.
_FUNC_MATCH_THRESHOLD = 0.25


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

    Key behaviour (v3)
    ------------------
    * Each subtask from the project is matched against EXISTING doc.function
      records on the module by Jaccard word-overlap between the task title and
      the function name.  Match threshold: 0.25.

    * If a match is found  -> fields (description/requirements/steps/result)
      are written ON THE EXISTING function.  The function keeps its original
      sequence/number/position in the document.

    * If no match is found -> a new doc.function is created.  Its sequence is
      set to max(existing sequences) + 10 so it appears after all auto-generated
      functions.  This case is the FALLBACK, not the main path.
    """

    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def enrich_module(self, doc_module, overwrite=False):
        """
        Enrich a single doc.module from project task snapshots.

        Returns dict with keys:
          module_enriched   (bool)
          menus_enriched    (int)
          functions_enriched (int)
          skipped           (int)
          reason            (str)
        """
        stats = {
            'module_enriched': False,
            'menus_enriched': 0,
            'functions_enriched': 0,
            'skipped': 0,
            'reason': 'not_started',
        }

        generation = doc_module.generation_id
        if not generation:
            stats['reason'] = 'no_generation'
            return stats

        technical_name = (doc_module.technical_name or '').lower().strip()
        if not technical_name:
            stats['reason'] = 'no_technical_name'
            return stats

        all_snaps = self._load_snaps_for_generation(generation)

        if not all_snaps:
            stats['reason'] = 'no_snapshots_configured'
            _logger.info(
                'enrich_module: module=%s — no snapshot set configured on '
                'generation id=%s.  Enrichment skipped.',
                technical_name, generation.id,
            )
            return stats

        _logger.info(
            'enrich_module: module=%s  generation=%s  total_snaps=%s',
            technical_name, generation.id, len(all_snaps),
        )

        parent_snaps = self._find_module_parent_snaps(technical_name, all_snaps)

        if not parent_snaps:
            stats['reason'] = 'no_matching_tasks'
            _logger.info(
                'enrich_module: module=%s — no tasks found with tag [%s].',
                technical_name, technical_name,
            )
            return stats

        _logger.info(
            'enrich_module: found %s parent snap(s) for [%s]: %s',
            len(parent_snaps), technical_name,
            [s.name for s in parent_snaps],
        )

        Snap = self.env['doc.project.task.snapshot']
        functional_snaps = Snap.browse()
        for parent in parent_snaps:
            functional_snaps |= parent.child_snapshot_ids

        if not functional_snaps:
            functional_snaps = Snap.browse([s.id for s in parent_snaps])

        _logger.info(
            'enrich_module: %s functional snap(s) for [%s]',
            len(functional_snaps), technical_name,
        )

        stats['module_enriched'] = self._enrich_module_description(
            doc_module, parent_snaps[0], overwrite=overwrite
        )

        stats['functions_enriched'] = self._upsert_functions_from_snaps(
            doc_module, functional_snaps, overwrite=overwrite
        )

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
        Snap = self.env['doc.project.task.snapshot']

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
    # Task -> module matching                                              #
    # ------------------------------------------------------------------ #

    def _find_module_parent_snaps(self, technical_name, all_snaps):
        result = [
            snap for snap in all_snaps
            if (snap.module_tag or '').lower() == technical_name
        ]
        if result:
            result.sort(key=lambda s: -len(s.child_snapshot_ids))
        return result

    # ------------------------------------------------------------------ #
    # doc.function upsert — IN-PLACE MATCHING (v3)                       #
    # ------------------------------------------------------------------ #

    def _upsert_functions_from_snaps(self, doc_module, functional_snaps, overwrite=False):
        """
        Match each subtask snapshot to an existing doc.function by name
        similarity (Jaccard >= 0.25).  If matched, enrich the existing
        function IN-PLACE (preserving sequence/position).  If not matched,
        create a new function appended after existing ones.

        Returns the count of functions enriched or created.
        """
        if not functional_snaps:
            return 0

        existing_funcs = list(doc_module.function_ids)

        # Build an index by source_task_id for exact matches (fast path)
        existing_by_task_id = {}
        for func in existing_funcs:
            key = getattr(func, 'source_task_id', 0) or 0
            if key:
                existing_by_task_id[key] = func

        # Compute max sequence for fallback new-function placement
        max_seq = max((f.sequence or 0 for f in existing_funcs), default=0)

        count = 0
        # Track which existing functions have already been matched so one
        # function is not enriched twice from different tasks.
        matched_func_ids = set()

        for idx, snap in enumerate(functional_snaps, start=1):
            sections = self._parse_subtask_sections(
                (snap.description_plain or '').strip()
            )

            title = (snap.name_clean or _clean_tag(snap.name) or snap.name or '').strip()
            if not title:
                continue

            desc = sections.get('description', '').strip()
            reqs = sections.get('requirements', '').strip()
            steps = sections.get('steps', '').strip()
            result_text = sections.get('result', '').strip()

            if not any([desc, reqs, steps, result_text]):
                desc = (snap.description_plain or '').strip()

            task_id = snap.original_task_id or 0

            # --- Step 1: try exact match by source_task_id ---
            func = existing_by_task_id.get(task_id)
            if func and func.id in matched_func_ids:
                func = None  # already used for another snap

            # --- Step 2: try fuzzy match by name (Jaccard) ---
            if not func:
                func = self._match_func_by_name(
                    title, existing_funcs, matched_func_ids,
                    threshold=_FUNC_MATCH_THRESHOLD,
                )

            if func:
                # ENRICH IN-PLACE — do not change sequence / number / position
                matched_func_ids.add(func.id)
                updates = {}
                if (overwrite or not func.description) and desc:
                    updates['description'] = desc
                if (overwrite or not getattr(func, 'requirements', None)) and reqs:
                    updates['requirements'] = reqs
                if (overwrite or not getattr(func, 'steps', None)) and steps:
                    updates['steps'] = steps
                if (overwrite or not getattr(func, 'result', None)) and result_text:
                    updates['result'] = result_text
                # Also store the source_task_id for future exact-match passes
                if task_id and 'source_task_id' in self.env['doc.function']._fields:
                    if not (getattr(func, 'source_task_id', 0) or 0):
                        updates['source_task_id'] = task_id
                if updates:
                    func.write(updates)
                    count += 1
                    _logger.info(
                        '_upsert_functions: IN-PLACE match  task="%s" -> func="%s" (id=%s)',
                        title, func.name, func.id,
                    )
                else:
                    _logger.debug(
                        '_upsert_functions: matched func id=%s already fully populated',
                        func.id,
                    )
            else:
                # No matching function found — create new at the end
                max_seq += 10
                vals = {
                    'doc_module_id': doc_module.id,
                    'name': title,
                    'sequence': max_seq,
                    'description': desc or False,
                }
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
                    '_upsert_functions: NO MATCH for task="%s" -> created new function',
                    title,
                )

        _logger.info(
            '_upsert_functions_from_snaps: module=%s  enriched/created %s functions',
            doc_module.technical_name, count,
        )
        return count

    def _match_func_by_name(self, task_title, existing_funcs, already_matched, threshold):
        """
        Find the best-matching doc.function for a task title.

        Uses Jaccard word-overlap on normalised names.  The candidate with the
        highest score above `threshold` that has not been matched yet is returned.

        Returns the matching doc.function record or None.
        """
        best_func = None
        best_score = 0.0
        for func in existing_funcs:
            if func.id in already_matched:
                continue
            score = _jaccard(task_title, func.name or '')
            if score > best_score:
                best_score = score
                best_func = func
        if best_func and best_score >= threshold:
            _logger.debug(
                '_match_func_by_name: "%s" -> "%s"  score=%.2f',
                task_title, best_func.name, best_score,
            )
            return best_func
        return None

    # ------------------------------------------------------------------ #
    # Menu caption enrichment (best-effort)                                #
    # ------------------------------------------------------------------ #

    def _enrich_menu_caption(self, menu, snaps, overwrite=False, threshold=0.15):
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

    def _best_snap_match(self, name, snaps, threshold=0.15):
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
          \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435   -> 'description'
          \u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f -> 'requirements'
          \u041f\u043e\u0440\u044f\u0434\u043e\u043a    -> 'steps'
          \u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442  -> 'result'
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
