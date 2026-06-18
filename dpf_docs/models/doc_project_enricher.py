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
   b. Subtasks are matched against EXISTING doc.function records by
      source_task_id (exact) or Jaccard name similarity >= 0.25 (fuzzy).
      If matched  -> function enriched IN-PLACE (sequence/position unchanged).
      If NOT matched -> a new function is created and inserted AFTER the
      LAST function in the same thematic group (smart group-end placement).
      Placement uses a bilingual keyword map so Russian task names are
      correctly matched against English function names.
   c. Menu captions receive a best-effort fill from Jaccard similarity.

Sequence placement algorithm (v6)
----------------------------------
When no in-place match exists for a task the enricher must decide WHERE
to insert the new doc.function so it appears next to related content
instead of falling to the very end of the document.

Old v5 approach — insert at (best_neighbour.sequence + 5) — caused the
new function to land in the MIDDLE of an existing thematic group when
there were several functions sharing the same topic (e.g. multiple
"Venue" functions).  The fix in v6:

  1. Find the best neighbour (highest _keyword_overlap score).
  2. Identify the topic keywords that drove that match.
  3. Walk ALL existing functions; collect those whose _keyword_overlap
     with the same topic also exceeds the placement threshold.
  4. The insertion point is (max_sequence_in_group + 10).

This guarantees the new entry always lands AFTER the whole thematic group,
never in the middle of it.
"""
import logging
import re
import unicodedata

from odoo import _, models

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'^\[[\w]+\]\s*', flags=re.UNICODE)
_NOISE_RE = re.compile(
    r'\u0422\u0417|\u0422\u0421|\xa7[\d\.]+|\xa7\d|\[.*?\]'
    r'|["\u00ab\u00bb()/\\,\.\!\?\-\u2013\u2014]',
    flags=re.UNICODE,
)
_SECTION_RE = re.compile(
    r'^\s*(?P<key>\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435'
    r'|\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f'
    r'|\u041f\u043e\u0440\u044f\u0434\u043e\u043a'
    r'|\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442)\s*:?\s*$',
    flags=re.UNICODE | re.IGNORECASE,
)

# Threshold for in-place matching (Jaccard).
_FUNC_MATCH_THRESHOLD = 0.25

# Placement threshold: any score > 0 counts as "same topic".
_PLACEMENT_THRESHOLD = 0.0

# ---------------------------------------------------------------------------
# Bilingual keyword map: Russian words in task names -> English keywords
# that may appear in existing (auto-generated) function names.
# ---------------------------------------------------------------------------
_KW_MAP: dict[str, frozenset] = {
    # Events
    'мероприятие':   frozenset({'event', 'events', 'management', 'router'}),
    'мероприятия':   frozenset({'event', 'events', 'management'}),
    'создание':      frozenset({'create', 'creation', 'new', 'event', 'events'}),
    'управление':    frozenset({'management', 'manage', 'router', 'event'}),
    'конференц':     frozenset({'event', 'events', 'room', 'rooms'}),
    'конференции':   frozenset({'event', 'events', 'conference'}),
    'выставки':      frozenset({'event', 'events', 'exhibition'}),
    'лекции':        frozenset({'event', 'events', 'lecture'}),
    # Rooms
    'помещение':     frozenset({'room', 'rooms', 'venue', 'venues', 'space'}),
    'помещения':     frozenset({'room', 'rooms', 'venue', 'venues'}),
    'зал':           frozenset({'room', 'rooms', 'hall', 'venue'}),
    'рассадка':      frozenset({'seating', 'room', 'rooms', 'venue', 'layout'}),
    'схема':         frozenset({'layout', 'schema', 'room', 'venue'}),
    'визуализация':  frozenset({'room', 'venue', 'layout', 'display'}),
    # Venues
    'площадка':      frozenset({'venue', 'venues', 'location'}),
    'площадки':      frozenset({'venue', 'venues', 'location'}),
    'место':         frozenset({'venue', 'venues', 'location', 'place'}),
    'места':         frozenset({'venue', 'venues', 'seats', 'location'}),
    'проведения':    frozenset({'venue', 'venues', 'location', 'event'}),
    # Resources
    'ресурс':        frozenset({'resource', 'resources', 'equipment'}),
    'ресурсы':       frozenset({'resource', 'resources', 'equipment'}),
    'бронирование':  frozenset({'booking', 'reservation', 'resource', 'equipment'}),
    'пересечений':   frozenset({'conflict', 'overlap', 'resource', 'booking'}),
    'пересечения':   frozenset({'conflict', 'overlap', 'resource'}),
    'контроль':      frozenset({'control', 'check', 'resource', 'booking'}),
    # Agenda
    'программа':     frozenset({'agenda', 'program', 'schedule'}),
    'программы':     frozenset({'agenda', 'program', 'schedule'}),
    'регламент':     frozenset({'agenda', 'schedule', 'regulation'}),
    'выступлений':   frozenset({'agenda', 'speaker', 'speech'}),
    # Equipment
    'оборудование':  frozenset({'equipment', 'gear', 'resource'}),
    'оборудования':  frozenset({'equipment', 'gear', 'resource'}),
    'внеплановые':   frozenset({'equipment', 'unplanned', 'ad-hoc'}),
    'доступность':   frozenset({'availability', 'equipment', 'resource'}),
    # Registrations
    'регистрация':   frozenset({'registration', 'registrations', 'signup'}),
    'регистрации':   frozenset({'registration', 'registrations', 'signup'}),
    'участников':    frozenset({'registration', 'registrations', 'participant', 'attendee'}),
    'участники':     frozenset({'participant', 'attendee', 'registration'}),
    'роли':          frozenset({'role', 'roles', 'registration', 'access'}),
    'подтверждение': frozenset({'confirmation', 'approve', 'registration'}),
    # Notifications
    'уведомления':   frozenset({'notification', 'notifications', 'push', 'email'}),
    'уведомление':   frozenset({'notification', 'notifications', 'push', 'email'}),
    'push':          frozenset({'notification', 'push'}),
    'email':         frozenset({'email', 'notification'}),
    # Media
    'медиа':         frozenset({'media', 'gallery', 'photo', 'video'}),
    'медиагалерея':  frozenset({'media', 'gallery', 'library'}),
    'фото':          frozenset({'photo', 'image', 'media', 'gallery'}),
    'видео':         frozenset({'video', 'media', 'gallery'}),
    'галерея':       frozenset({'gallery', 'media', 'library'}),
    # Analytics
    'аналитика':     frozenset({'analytics', 'report', 'statistics', 'stats'}),
    'отчёт':         frozenset({'report', 'analytics', 'export'}),
    'отчет':         frozenset({'report', 'analytics', 'export'}),
    'статистика':    frozenset({'statistics', 'analytics', 'report'}),
    'история':       frozenset({'history', 'log', 'order', 'analytics'}),
    'статус':        frozenset({'status', 'order', 'state'}),
    'заказов':       frozenset({'order', 'orders', 'history'}),
    # Export
    'экспорт':       frozenset({'export', 'report', 'pdf', 'xlsx'}),
    'pdf':           frozenset({'pdf', 'report', 'export'}),
    'xlsx':          frozenset({'xlsx', 'export', 'report'}),
}


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


def _topic_keywords(task_title: str) -> frozenset:
    """
    Return the union of all English keyword sets that map from words
    in task_title via _KW_MAP.  Used to identify the "topic" so we can
    find the entire thematic group, not just the single best neighbour.
    """
    words = _normalize(task_title).split()
    result: set[str] = set()
    for word in words:
        mapped = _KW_MAP.get(word)
        if mapped:
            result |= mapped
    # Also add the normalised task words themselves (handles EN task names)
    result |= set(words)
    return frozenset(result)


def _keyword_overlap(task_title: str, func_name: str) -> float:
    """
    Cross-language similarity score in [0.0, 1.0].

    1. Try Jaccard first (works for same-language pairs).
    2. If Jaccard == 0: translate Russian words via _KW_MAP and check
       how many translated English keywords appear in func_name tokens.

    Score > 0 means "thematically related" — sufficient for placement.
    Score always stays below _FUNC_MATCH_THRESHOLD (0.25) for cross-language
    hits, so keyword-only matches never trigger IN-PLACE enrichment.
    """
    jac = _jaccard(task_title, func_name)
    if jac > 0:
        return jac

    task_words = _normalize(task_title).split()
    func_tokens = set(_normalize(func_name).split())

    if not task_words or not func_tokens:
        return 0.0

    translated: set[str] = set()
    for word in task_words:
        mapped = _KW_MAP.get(word)
        if mapped:
            translated |= mapped

    if not translated:
        return 0.0

    hits = translated & func_tokens
    if not hits:
        return 0.0

    # Capped at 0.20 — always below _FUNC_MATCH_THRESHOLD so it only
    # influences placement, never in-place matching.
    return min(len(hits) / len(translated), 0.20)


def _find_group_end(topic_kws: frozenset, existing_funcs: list, skip_threshold: float = 0.0) -> int:
    """
    Given a set of topic keywords, find the maximum sequence value among
    all existing functions whose name shares at least one token with
    topic_kws (after normalization).

    Returns the max sequence of the thematic group, or -1 if no group found.

    This prevents new functions from being inserted IN THE MIDDLE of a
    thematic group when several functions share the same topic.
    """
    if not topic_kws or not existing_funcs:
        return -1

    group_max_seq = -1
    for func in existing_funcs:
        func_tokens = set(_normalize(func.name or '').split())
        if not func_tokens:
            continue
        overlap = topic_kws & func_tokens
        if overlap:
            seq = func.sequence or 0
            if seq > group_max_seq:
                group_max_seq = seq
                _logger.debug(
                    '_find_group_end: func="%s" seq=%s is in topic group (overlap=%s)',
                    func.name, seq, overlap,
                )
    return group_max_seq


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class DocProjectEnricher(models.AbstractModel):
    """
    Enriches doc.module / doc.menu / doc.function records with project task data.

    Key behaviour (v6)
    ------------------
    * Each subtask from the project is matched against EXISTING doc.function
      records on the module by:
        1. Exact match on source_task_id  (fast path)
        2. Jaccard word-overlap >= 0.25 on normalised names  (same-language fuzzy)

    * Match found  -> fields (description/requirements/steps/result) are
      written ON THE EXISTING function.  Sequence / number / position
      are NOT changed.

    * No match     -> a new doc.function is created.
      Placement uses _compute_insert_sequence (v6) which:
        a. Finds the best single neighbour via _keyword_overlap.
        b. Derives the topic keyword set from the task title.
        c. Walks ALL existing functions to find the LAST one that belongs
           to the same thematic group (_find_group_end).
        d. Inserts AFTER the group end (group_end_seq + 10).
      This ensures new functions always land after the WHOLE group, never
      in the middle of it.
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
          module_enriched    (bool)
          menus_enriched     (int)
          functions_enriched (int)
          skipped            (int)
          reason             (str)
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
    # doc.function upsert — IN-PLACE MATCHING + SMART GROUP PLACEMENT (v6)#
    # ------------------------------------------------------------------ #

    def _upsert_functions_from_snaps(self, doc_module, functional_snaps, overwrite=False):
        """
        Match each subtask snapshot to an existing doc.function by name
        similarity (Jaccard >= 0.25).  If matched, enrich IN-PLACE
        (sequence/position unchanged).

        If NOT matched, create a new function inserted AFTER THE LAST
        FUNCTION IN THE THEMATIC GROUP using _compute_insert_sequence (v6).

        Returns the count of functions enriched or created.
        """
        if not functional_snaps:
            return 0

        existing_funcs = list(doc_module.function_ids)

        # Build index by source_task_id for exact match (fast path)
        existing_by_task_id = {}
        for func in existing_funcs:
            key = getattr(func, 'source_task_id', 0) or 0
            if key:
                existing_by_task_id[key] = func

        # Max sequence for fallback (no thematic neighbour found)
        max_seq = max((f.sequence or 0 for f in existing_funcs), default=0)

        count = 0
        matched_func_ids = set()

        for snap in functional_snaps:
            sections = self._parse_subtask_sections(
                (snap.description_plain or '').strip()
            )

            title = (snap.name_clean or _clean_tag(snap.name) or snap.name or '').strip()
            if not title:
                continue

            desc        = sections.get('description',  '').strip()
            reqs        = sections.get('requirements', '').strip()
            steps       = sections.get('steps',        '').strip()
            result_text = sections.get('result',       '').strip()

            if not any([desc, reqs, steps, result_text]):
                desc = (snap.description_plain or '').strip()

            task_id = snap.original_task_id or 0

            # --- Step 1: exact match by source_task_id ---
            func = existing_by_task_id.get(task_id)
            if func and func.id in matched_func_ids:
                func = None

            # --- Step 2: fuzzy match by name (Jaccard only, no cross-lang) ---
            if not func:
                func = self._match_func_by_name(
                    title, existing_funcs, matched_func_ids,
                    threshold=_FUNC_MATCH_THRESHOLD,
                )

            if func:
                # ENRICH IN-PLACE — never change sequence / number
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
                if task_id and 'source_task_id' in self.env['doc.function']._fields:
                    if not (getattr(func, 'source_task_id', 0) or 0):
                        updates['source_task_id'] = task_id
                if updates:
                    func.write(updates)
                    count += 1
                    _logger.info(
                        '_upsert_functions: IN-PLACE  task="%s" -> func="%s" (id=%s)',
                        title, func.name, func.id,
                    )
                else:
                    _logger.debug(
                        '_upsert_functions: matched func id=%s already fully populated',
                        func.id,
                    )
            else:
                # No match — compute smart group-end placement
                insert_seq = self._compute_insert_sequence(title, existing_funcs, max_seq)

                # Shift all functions at or after insert_seq to make room
                for ef in existing_funcs:
                    if (ef.sequence or 0) >= insert_seq:
                        ef.write({'sequence': (ef.sequence or 0) + 10})

                vals = {
                    'doc_module_id': doc_module.id,
                    'name': title,
                    'sequence': insert_seq,
                    'description': desc or False,
                    'source': 'project',
                }
                for field_name, value in [
                    ('requirements', reqs or False),
                    ('steps',        steps or False),
                    ('result',       result_text or False),
                    ('source_task_id', task_id or False),
                ]:
                    if field_name in self.env['doc.function']._fields:
                        vals[field_name] = value

                new_func = self.env['doc.function'].create(vals)
                existing_funcs.append(new_func)
                max_seq = max(max_seq, insert_seq)
                count += 1
                _logger.info(
                    '_upsert_functions: NEW FUNC  task="%s"  seq=%s',
                    title, insert_seq,
                )

        _logger.info(
            '_upsert_functions_from_snaps: module=%s  enriched/created=%s',
            doc_module.technical_name, count,
        )
        return count

    def _compute_insert_sequence(self, task_title: str, existing_funcs: list, max_seq: int) -> int:
        """
        Compute sequence for a new function so it lands AFTER the entire
        thematic group that best matches the task title.

        Algorithm (v6)
        --------------
        1. Find the single best-scoring neighbour via _keyword_overlap.
        2. Derive the topic keyword set (_topic_keywords) from task_title.
        3. Find the LAST function in that topic group (_find_group_end).
        4. Insert at group_end + 10.

        Falls back to max_seq + 10 when no thematic neighbour is found.
        """
        best_func = None
        best_score = 0.0

        for func in existing_funcs:
            score = _keyword_overlap(task_title, func.name or '')
            if score > best_score:
                best_score = score
                best_func = func

        if not best_func or best_score <= _PLACEMENT_THRESHOLD:
            fallback = max_seq + 10
            _logger.debug(
                '_compute_insert_sequence: "%s" -> no neighbour, fallback seq=%s',
                task_title, fallback,
            )
            return fallback

        # Derive the topic keyword set from the task title
        topic_kws = _topic_keywords(task_title)

        # Find the LAST function in the thematic group
        group_end_seq = _find_group_end(topic_kws, existing_funcs)

        if group_end_seq < 0:
            # _find_group_end found nothing — fall back to best neighbour seq
            group_end_seq = best_func.sequence or 0

        insert_seq = group_end_seq + 10
        _logger.debug(
            '_compute_insert_sequence: "%s" -> best_neighbour="%s" '
            'group_end_seq=%s -> insert at %s',
            task_title, best_func.name, group_end_seq, insert_seq,
        )
        return insert_seq

    def _match_func_by_name(self, task_title, existing_funcs, already_matched, threshold):
        """
        Find the best-matching doc.function for a task title using Jaccard
        word-overlap on normalised names.

        Only same-language pairs (both RU or both EN) benefit from this;
        cross-language matching is intentionally NOT done here — use
        _keyword_overlap for placement only.
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
            raw   = snap.name or ''
            clean = snap.name_clean or _clean_tag(raw)
            score = max(_jaccard(raw, name), _jaccard(clean, name))
            if score > best_score:
                best_score = score
                best_snap  = snap
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
          Описание   -> 'description'
          Требования -> 'requirements'
          Порядок    -> 'steps'
          Результат  -> 'result'

        Lines under «ЧТО СДЕЛАТЬ» / «ЧТО НУЖНО СДЕЛАТЬ» are discarded
        — they are implementation notes, not documentation content.
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
        _DISCARD_HEADERS = {
            '\u0447\u0442\u043e \u0441\u0434\u0435\u043b\u0430\u0442\u044c',
            '\u0447\u0442\u043e \u043d\u0443\u0436\u043d\u043e \u0441\u0434\u0435\u043b\u0430\u0442\u044c',
        }

        _SECTION_DETECT = re.compile(
            r'^\s*(?P<key>[^\n:]{2,40})\s*:?\s*$',
            flags=re.UNICODE | re.IGNORECASE,
        )

        current_key  = 'description'
        discard_mode = False
        buckets      = {k: [] for k in result}

        for line in plain_text.splitlines():
            m = _SECTION_DETECT.match(line)
            if m:
                raw_key = m.group('key').strip().lower()
                if raw_key in _DISCARD_HEADERS:
                    discard_mode = True
                    continue
                mapped = _KEY_MAP.get(raw_key)
                if mapped:
                    current_key  = mapped
                    discard_mode = False
                    continue
                if discard_mode:
                    continue

            if discard_mode:
                continue
            buckets[current_key].append(line)

        for k, bucket in buckets.items():
            result[k] = '\n'.join(bucket).strip()

        if not any([result['requirements'], result['steps'], result['result']]):
            result['description'] = plain_text.strip()

        return result
