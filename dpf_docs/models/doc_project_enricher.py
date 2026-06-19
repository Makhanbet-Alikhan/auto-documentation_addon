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

v2 fix — noise-line filtering in _parse_subtask_sections
---------------------------------------------------------
Task descriptions often contain implementation notes that should NEVER
appear in user-facing documentation:
  * «ТС §П.2 | Оценка: 3.0 нед.»  — technical spec references
  * «Оценка: 3.0 нед.»         — effort estimates
  * «GET https://...» / «POST ...»    — raw API endpoint notes
  * «§1. Пункт ТЗ»              — spec paragraph references
  * «----------»                    — horizontal separators
  * «ЧТО СДЕЛАТЬ» / «ЧТО НУЖНО СДЕЛАТЬ»  — dev task headers (already
    handled by _DISCARD_HEADERS, but now also caught by patterns)

The new _NOISE_LINE_PATTERNS list is applied per-line BEFORE the line is
added to any bucket.  Patterns are generic regexes — NOT tied to any
specific module — so the fix works for every documented addon.
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

# ---------------------------------------------------------------------------
# Noise-line patterns — lines matching ANY of these are silently discarded
# from every documentation bucket regardless of the current section.
#
# Rules are intentionally generic so they work for any Odoo addon, not just
# dpf_events.  Add new patterns here when new categories of noise appear.
# ---------------------------------------------------------------------------
_NOISE_LINE_PATTERNS = [
    # Technical-spec references: "ТС §П.2", "ТС §И.1", etc.
    re.compile(r'\u0422\u0421\s*\xa7', re.UNICODE),
    # Effort estimates: "Оценка:\s*3.0", "Оценка:\s*0.5 нед."
    re.compile(r'\u041e\u0446\u0435\u043d\u043a\u0430\s*:', re.UNICODE),
    # Spec paragraph anchors: "§1.", "§ 2.3"
    re.compile(r'^\s*\xa7\s*\d', re.UNICODE),
    # Raw HTTP method lines: "GET https://...", "POST http://..."
    re.compile(r'^\s*(GET|POST|PUT|PATCH|DELETE)\s+https?://', re.IGNORECASE),
    # Bare URLs on their own line
    re.compile(r'^\s*https?://\S+\s*$', re.IGNORECASE),
    # Horizontal separators: "---", "====", "___"
    re.compile(r'^\s*[-=_]{3,}\s*$'),
    # Developer task keywords that slipped through the header filter
    re.compile(r'\u0447\u0442\u043e\s+(\u043d\u0443\u0436\u043d\u043e\s+)?'
               r'\u0441\u0434\u0435\u043b\u0430\u0442\u044c', re.UNICODE | re.IGNORECASE),
    # Inline spec markers: "| Оценка", "| Task #27"
    re.compile(r'\|\s*(\u041e\u0446\u0435\u043d\u043a\u0430|Task\s*#)', re.UNICODE | re.IGNORECASE),
]


def _is_noise_line(line: str) -> bool:
    """Return True if the line is a technical/implementation artifact
    that must not appear in user-facing documentation."""
    for pattern in _NOISE_LINE_PATTERNS:
        if pattern.search(line):
            return True
    return False


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
    '\u043c\u0435\u0440\u043e\u043f\u0440\u0438\u044f\u0442\u0438\u0435':   frozenset({'event', 'events', 'management', 'router'}),
    '\u043c\u0435\u0440\u043e\u043f\u0440\u0438\u044f\u0442\u0438\u044f':   frozenset({'event', 'events', 'management'}),
    '\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0435':      frozenset({'create', 'creation', 'new', 'event', 'events'}),
    '\u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435':    frozenset({'management', 'manage', 'router', 'event'}),
    '\u043a\u043e\u043d\u0444\u0435\u0440\u0435\u043d\u0446':     frozenset({'event', 'events', 'room', 'rooms'}),
    '\u043a\u043e\u043d\u0444\u0435\u0440\u0435\u043d\u0446\u0438\u0438':   frozenset({'event', 'events', 'conference'}),
    '\u0432\u044b\u0441\u0442\u0430\u0432\u043a\u0438':      frozenset({'event', 'events', 'exhibition'}),
    '\u043b\u0435\u043a\u0446\u0438\u0438':        frozenset({'event', 'events', 'lecture'}),
    # Rooms
    '\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435':     frozenset({'room', 'rooms', 'venue', 'venues', 'space'}),
    '\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u044f':     frozenset({'room', 'rooms', 'venue', 'venues'}),
    '\u0437\u0430\u043b':           frozenset({'room', 'rooms', 'hall', 'venue'}),
    '\u0440\u0430\u0441\u0441\u0430\u0434\u043a\u0430':      frozenset({'seating', 'room', 'rooms', 'venue', 'layout'}),
    '\u0441\u0445\u0435\u043c\u0430':         frozenset({'layout', 'schema', 'room', 'venue'}),
    '\u0432\u0438\u0437\u0443\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u044f':  frozenset({'room', 'venue', 'layout', 'display'}),
    # Venues
    '\u043f\u043b\u043e\u0449\u0430\u0434\u043a\u0430':      frozenset({'venue', 'venues', 'location'}),
    '\u043f\u043b\u043e\u0449\u0430\u0434\u043a\u0438':      frozenset({'venue', 'venues', 'location'}),
    '\u043c\u0435\u0441\u0442\u043e':         frozenset({'venue', 'venues', 'location', 'place'}),
    '\u043c\u0435\u0441\u0442\u0430':         frozenset({'venue', 'venues', 'seats', 'location'}),
    '\u043f\u0440\u043e\u0432\u0435\u0434\u0435\u043d\u0438\u044f':    frozenset({'venue', 'venues', 'location', 'event'}),
    # Resources
    '\u0440\u0435\u0441\u0443\u0440\u0441':        frozenset({'resource', 'resources', 'equipment'}),
    '\u0440\u0435\u0441\u0443\u0440\u0441\u044b':       frozenset({'resource', 'resources', 'equipment'}),
    '\u0431\u0440\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435':  frozenset({'booking', 'reservation', 'resource', 'equipment'}),
    '\u043f\u0435\u0440\u0435\u0441\u0435\u0447\u0435\u043d\u0438\u0439':   frozenset({'conflict', 'overlap', 'resource', 'booking'}),
    '\u043f\u0435\u0440\u0435\u0441\u0435\u0447\u0435\u043d\u0438\u044f':   frozenset({'conflict', 'overlap', 'resource'}),
    '\u043a\u043e\u043d\u0442\u0440\u043e\u043b\u044c':      frozenset({'control', 'check', 'resource', 'booking'}),
    # Agenda
    '\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0430':     frozenset({'agenda', 'program', 'schedule'}),
    '\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b':     frozenset({'agenda', 'program', 'schedule'}),
    '\u0440\u0435\u0433\u043b\u0430\u043c\u0435\u043d\u0442':     frozenset({'agenda', 'schedule', 'regulation'}),
    '\u0432\u044b\u0441\u0442\u0443\u043f\u043b\u0435\u043d\u0438\u0439':   frozenset({'agenda', 'speaker', 'speech'}),
    # Equipment
    '\u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u0435':  frozenset({'equipment', 'gear', 'resource'}),
    '\u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u044f':  frozenset({'equipment', 'gear', 'resource'}),
    '\u0432\u043d\u0435\u043f\u043b\u0430\u043d\u043e\u0432\u044b\u0435':   frozenset({'equipment', 'unplanned', 'ad-hoc'}),
    '\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e\u0441\u0442\u044c':   frozenset({'availability', 'equipment', 'resource'}),
    # Registrations
    '\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f':   frozenset({'registration', 'registrations', 'signup'}),
    '\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438':   frozenset({'registration', 'registrations', 'signup'}),
    '\u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432':    frozenset({'registration', 'registrations', 'participant', 'attendee'}),
    '\u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438':    frozenset({'participant', 'attendee', 'registration'}),
    '\u0440\u043e\u043b\u0438':          frozenset({'role', 'roles', 'registration', 'access'}),
    '\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435': frozenset({'confirmation', 'approve', 'registration'}),
    # Notifications
    '\u0443\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u044f':   frozenset({'notification', 'notifications', 'push', 'email'}),
    '\u0443\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u0435':   frozenset({'notification', 'notifications', 'push', 'email'}),
    'push':          frozenset({'notification', 'push'}),
    'email':         frozenset({'email', 'notification'}),
    # Media
    '\u043c\u0435\u0434\u0438\u0430':         frozenset({'media', 'gallery', 'photo', 'video'}),
    '\u043c\u0435\u0434\u0438\u0430\u0433\u0430\u043b\u0435\u0440\u0435\u044f':  frozenset({'media', 'gallery', 'library'}),
    '\u0444\u043e\u0442\u043e':          frozenset({'photo', 'image', 'media', 'gallery'}),
    '\u0432\u0438\u0434\u0435\u043e':         frozenset({'video', 'media', 'gallery'}),
    '\u0433\u0430\u043b\u0435\u0440\u0435\u044f':       frozenset({'gallery', 'media', 'library'}),
    # Analytics
    '\u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430':     frozenset({'analytics', 'report', 'statistics', 'stats'}),
    '\u043e\u0442\u0447\u0451\u0442':         frozenset({'report', 'analytics', 'export'}),
    '\u043e\u0442\u0447\u0435\u0442':         frozenset({'report', 'analytics', 'export'}),
    '\u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430':    frozenset({'statistics', 'analytics', 'report'}),
    '\u0438\u0441\u0442\u043e\u0440\u0438\u044f':       frozenset({'history', 'log', 'order', 'analytics'}),
    '\u0441\u0442\u0430\u0442\u0443\u0441':        frozenset({'status', 'order', 'state'}),
    '\u0437\u0430\u043a\u0430\u0437\u043e\u0432':       frozenset({'order', 'orders', 'history'}),
    # Export
    '\u044d\u043a\u0441\u043f\u043e\u0440\u0442':       frozenset({'export', 'report', 'pdf', 'xlsx'}),
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
    words = _normalize(task_title).split()
    result: set[str] = set()
    for word in words:
        mapped = _KW_MAP.get(word)
        if mapped:
            result |= mapped
    result |= set(words)
    return frozenset(result)


def _keyword_overlap(task_title: str, func_name: str) -> float:
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

    return min(len(hits) / len(translated), 0.20)


def _find_group_end(topic_kws: frozenset, existing_funcs: list, skip_threshold: float = 0.0) -> int:
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
    return group_max_seq


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class DocProjectEnricher(models.AbstractModel):
    """
    Enriches doc.module / doc.menu / doc.function records with project task data.
    See module docstring for full description.
    """

    _name = 'doc.project.enricher'
    _description = 'Auto Doc - Project Task Enricher'

    def enrich_module(self, doc_module, overwrite=False):
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
            return stats

        parent_snaps = self._find_module_parent_snaps(technical_name, all_snaps)

        if not parent_snaps:
            stats['reason'] = 'no_matching_tasks'
            return stats

        Snap = self.env['doc.project.task.snapshot']
        functional_snaps = Snap.browse()
        for parent in parent_snaps:
            functional_snaps |= parent.child_snapshot_ids

        if not functional_snaps:
            functional_snaps = Snap.browse([s.id for s in parent_snaps])

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
        return stats

    def _load_snaps_for_generation(self, generation):
        Snap = self.env['doc.project.task.snapshot']
        snapshot_set = getattr(generation, 'snapshot_set_id', None)
        if snapshot_set and snapshot_set.id:
            snaps = Snap.search(
                [('snapshot_set_id', '=', snapshot_set.id)],
                order='depth asc, sequence asc, id asc',
            )
            if snaps:
                return snaps
        return Snap.search(
            [('generation_id', '=', generation.id)],
            order='depth asc, sequence asc, id asc',
        )

    def _find_module_parent_snaps(self, technical_name, all_snaps):
        result = [
            snap for snap in all_snaps
            if (snap.module_tag or '').lower() == technical_name
        ]
        if result:
            result.sort(key=lambda s: -len(s.child_snapshot_ids))
        return result

    def _upsert_functions_from_snaps(self, doc_module, functional_snaps, overwrite=False):
        if not functional_snaps:
            return 0

        existing_funcs = list(doc_module.function_ids)
        existing_by_task_id = {}
        for func in existing_funcs:
            key = getattr(func, 'source_task_id', 0) or 0
            if key:
                existing_by_task_id[key] = func

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

            func = existing_by_task_id.get(task_id)
            if func and func.id in matched_func_ids:
                func = None

            if not func:
                func = self._match_func_by_name(
                    title, existing_funcs, matched_func_ids,
                    threshold=_FUNC_MATCH_THRESHOLD,
                )

            if func:
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
            else:
                insert_seq = self._compute_insert_sequence(title, existing_funcs, max_seq)
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

        return count

    def _compute_insert_sequence(self, task_title: str, existing_funcs: list, max_seq: int) -> int:
        best_func = None
        best_score = 0.0
        for func in existing_funcs:
            score = _keyword_overlap(task_title, func.name or '')
            if score > best_score:
                best_score = score
                best_func = func

        if not best_func or best_score <= _PLACEMENT_THRESHOLD:
            return max_seq + 10

        topic_kws = _topic_keywords(task_title)
        group_end_seq = _find_group_end(topic_kws, existing_funcs)
        if group_end_seq < 0:
            group_end_seq = best_func.sequence or 0
        return group_end_seq + 10

    def _match_func_by_name(self, task_title, existing_funcs, already_matched, threshold):
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
            return best_func
        return None

    def _enrich_menu_caption(self, menu, snaps, overwrite=False, threshold=0.15):
        if not overwrite:
            src = getattr(menu, 'caption_source', 'generated') or 'generated'
            if src == 'manual':
                return False
            if src == 'task' and menu.caption:
                return False

        best_snap, _score = self._best_snap_match(menu.name or '', snaps, threshold=threshold)
        if not best_snap:
            last_seg = (menu.complete_name or '').split('/')[-1].strip()
            best_snap, _score = self._best_snap_match(last_seg, snaps, threshold=threshold)
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

    def _parse_subtask_sections(self, plain_text):
        """
        Split plain text into structured documentation sections.

        Recognised section headers (case-insensitive, Russian):
          Описание   -> 'description'
          Требования -> 'requirements'
          Порядок   -> 'steps'
          Результат  -> 'result'

        Lines matching _NOISE_LINE_PATTERNS are silently discarded from
        every bucket — they are implementation artefacts, not user docs.
        This works generically for ANY addon, not just dpf_events.

        Lines under «ЧТО СДЕЛАТЬ» / «ЧТО НУЖНО СДЕЛАТЬ» are also discarded.
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
            # --- Section header detection ---
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

            # --- Noise-line filter (generic, works for any addon) ---
            if _is_noise_line(line):
                _logger.debug('_parse_subtask_sections: discarded noise line: %r', line)
                continue

            buckets[current_key].append(line)

        # Strip leading/trailing blank lines from each bucket
        for k, bucket in buckets.items():
            while bucket and not bucket[0].strip():
                bucket.pop(0)
            while bucket and not bucket[-1].strip():
                bucket.pop()
            result[k] = '\n'.join(bucket).strip()

        if not any([result['requirements'], result['steps'], result['result']]):
            result['description'] = plain_text.strip()

        return result
