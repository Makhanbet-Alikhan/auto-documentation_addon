# -*- coding: utf-8 -*-
"""Generic helpers for user-facing model documentation.

These rules are intentionally module-agnostic. They produce a small,
readable field set for ANY addon by:
- removing technical/system chatter (message_*, activity_*, create_uid, etc.);
- preferring fields introduced by the documented addon (custom prefix match);
- keeping business-critical required / relational / status fields;
- limiting the appendix table to a readable size (max_fields cap).

This module has zero Odoo ORM dependencies and can be used from any
service, model or wizard in dpf_docs.
"""

SYSTEM_FIELD_NAMES = {
    'id', 'display_name', '__last_update',
    'create_uid', 'create_date', 'write_uid', 'write_date',
    'message_is_follower', 'message_follower_ids', 'message_partner_ids',
    'message_ids', 'has_message', 'message_needaction',
    'message_needaction_counter', 'message_has_error',
    'message_has_error_counter', 'message_attachment_count',
    'website_message_ids', 'message_has_sms_error',
    'activity_ids', 'activity_state', 'activity_user_id',
    'activity_type_id', 'activity_type_icon', 'activity_date_deadline',
    'my_activity_date_deadline', 'activity_summary',
    'activity_exception_decoration', 'activity_exception_icon',
    'access_url', 'access_token', 'access_warning',
}

SYSTEM_PREFIXES = (
    'message_', 'activity_', 'website_message_', '__',
)

BORING_LABELS = {
    'Display Name', 'ID', 'Created by', 'Created on', 'Last Updated by',
    'Last Updated on', 'Followers', 'Followers (Partners)', 'Messages',
    'Has Message', 'Action Needed', 'Number of Actions',
    'Message Delivery error', 'Number of errors', 'Attachment Count',
    'Website Messages', 'SMS Delivery error', 'Icon', 'Activity State',
    'Responsible User', 'Next Activity Type', 'Next Activity Deadline',
    'My Activity Deadline', 'Next Activity Summary',
    'Activity Exception Decoration',
}

BUSINESS_NAME_HINTS = (
    'name', 'title', 'code', 'number', 'reference', 'ref',
    'date_begin', 'date_end', 'date', 'state', 'status', 'stage',
    'type', 'category', 'role', 'partner', 'contact', 'email', 'phone',
    'room', 'venue', 'event', 'schedule', 'track', 'equipment', 'file',
    'image', 'address', 'capacity', 'color', 'sequence', 'active',
    'amount', 'price', 'qty', 'quantity', 'product', 'order', 'invoice',
    'project', 'task', 'employee', 'user', 'company', 'currency',
)

HEAVY_TYPES = {'binary', 'html', 'text'}


def derive_module_prefixes(module_name, model_names=None, field_names=None):
    """Derive likely technical prefixes for fields added by this addon.

    E.g. module 'dpf_events' yields prefixes ['dpf_', 'events_']
    so fields like dpf_state, dpf_room_id are ranked higher.
    """
    prefixes = set()
    if module_name:
        parts = [
            p for p in module_name.replace('-', '_').split('_')
            if p and p not in {'module', 'addon', 'odoo', 'custom'}
        ]
        for p in parts:
            if len(p) >= 3:
                prefixes.add(p + '_')
    for name in model_names or []:
        for token in str(name).replace('.', '_').split('_'):
            if len(token) >= 3:
                prefixes.add(token + '_')
    for name in field_names or []:
        for token in str(name).split('_'):
            if len(token) >= 3 and token not in {'name', 'date', 'state', 'type', 'ids'}:
                prefixes.add(token + '_')
    return sorted(prefixes)


def is_system_field(field_info):
    """Return True for system/chatter/technical fields that users never fill in."""
    name = field_info.get('name') or ''
    label = field_info.get('description') or ''
    if name in SYSTEM_FIELD_NAMES:
        return True
    if any(name.startswith(p) for p in SYSTEM_PREFIXES):
        return True
    if label in BORING_LABELS:
        return True
    return False


def is_user_visible_candidate(field_info):
    """Return True for fields worth showing in user-facing documentation."""
    if is_system_field(field_info):
        return False
    # related+readonly without required = display-only derived value
    if field_info.get('related') and field_info.get('readonly') and not field_info.get('required'):
        return False
    # non-stored compute = ephemeral, never stored, no user action needed
    if field_info.get('compute') and not field_info.get('store'):
        return False
    return True


def field_priority(field_info, module_prefixes):
    """Score a field 0-200. Higher = more important for the user manual."""
    name = field_info.get('name') or ''
    label = field_info.get('description') or ''
    ttype = field_info.get('ttype') or field_info.get('type') or ''
    required = bool(field_info.get('required'))
    readonly = bool(field_info.get('readonly'))
    custom = bool(field_info.get('is_custom'))
    relation = ttype in {'many2one', 'many2many', 'one2many'}
    has_prefix = any(name.startswith(p) for p in module_prefixes)
    keyword_match = any(h in name for h in BUSINESS_NAME_HINTS)

    penalty = 0
    if readonly and not required:
        penalty += 10
    if ttype in HEAVY_TYPES and not required:
        penalty += 2
    if name == 'active':
        penalty += 3
    if label in BORING_LABELS:
        penalty += 50

    score = 0
    score += 100 if has_prefix else 0
    score += 60 if custom else 0
    score += 35 if required else 0
    score += 20 if relation else 0
    score += 12 if keyword_match else 0
    score -= penalty
    return score


def compact_field_table(field_rows, module_name=None, model_names=None, max_fields=12):
    """Return a compacted, ranked list of fields for user-facing documentation.

    Args:
        field_rows: list of dicts with keys: name, description/string, ttype/type,
                    required, readonly, compute, store, related, is_custom, help.
        module_name: technical module name (e.g. 'dpf_events') for prefix detection.
        model_names: list of model technical names for additional prefix hints.
        max_fields: hard cap on number of fields returned.

    Returns:
        Filtered, ranked list of field dicts. System/chatter fields are always removed.
        If total passes filter <= max_fields, all are returned without truncation.
    """
    rows = [r for r in (field_rows or []) if is_user_visible_candidate(r)]
    prefixes = derive_module_prefixes(
        module_name,
        model_names,
        [r.get('name') for r in rows],
    )
    ranked = sorted(
        rows,
        key=lambda r: (
            -field_priority(r, prefixes),
            not bool(r.get('required')),
            (r.get('name') or '').lower(),
        )
    )
    if len(ranked) <= max_fields:
        return ranked
    # First try: only high-confidence business fields
    strong = [r for r in ranked if field_priority(r, prefixes) >= 70]
    keep = strong[:max_fields]
    # Fallback: just top N by rank
    if len(keep) < min(6, max_fields):
        keep = ranked[:max_fields]
    return keep
