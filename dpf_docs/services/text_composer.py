# -*- coding: utf-8 -*-
"""Compose human-readable prose from collected metadata.

Pure-python and side-effect free. Two layers:

* deterministic composers that turn structured metadata into readable text
  with no external dependency (the default, free, reproducible path);
* a thin ``llm_caption`` hook that an integrator can wire to any multimodal /
  text model. It is optional and never required for the module to work.
"""

# Fields that are always present in every Odoo model but are never shown
# on a "New record" form. We skip them to keep the field table focused on
# the fields a user actually fills in.
_SYSTEM_FIELDS = frozenset({
    "id",
    "display_name",
    "create_uid",
    "create_date",
    "write_uid",
    "write_date",
    "__last_update",
    "active",           # toggled via Archive/Unarchive, not a form input
    "message_ids",
    "message_follower_ids",
    "message_partner_ids",
    "message_is_follower",
    "message_unread_counter",
    "message_attachment_count",
    "message_has_error",
    "message_has_error_counter",
    "message_needaction",
    "message_needaction_counter",
    "activity_ids",
    "activity_state",
    "activity_user_id",
    "activity_type_id",
    "activity_type_icon",
    "activity_date_deadline",
    "my_activity_date_deadline",
    "activity_summary",
    "activity_exception_decoration",
    "activity_exception_icon",
    "website_message_ids",
    "has_message",
})


def compose_module_description(manifest, main_model_doc):
    """Build the top-level module description.

    :param manifest: dict-like manifest data (``summary``, ``description`` ...).
    :param main_model_doc: docstring of the module's primary model, or None.
    """
    parts = []
    summary = (manifest or {}).get("summary")
    if summary:
        parts.append(summary.strip())
    description = (manifest or {}).get("description")
    if description:
        parts.append(description.strip())
    if main_model_doc:
        parts.append(main_model_doc.strip())
    if not parts:
        return "No description available for this module."
    return "\n\n".join(parts)


def compose_model_description(model_name, class_doc, field_comments):
    """Build a model paragraph from its docstring and per-field comments."""
    lines = []
    if class_doc:
        lines.append(class_doc.strip())
    else:
        lines.append("Model %s." % model_name)
    if field_comments:
        annotated = [c for c in field_comments.values() if c]
        if annotated:
            lines.append("This model documents %d annotated field(s)."
                         % len(annotated))
    return "\n\n".join(lines)


def compose_menu_caption(menu_name, res_model, view_modes, fields_meta):
    """Deterministic description of a single screen (no LLM needed).

    Produces something like:

        "Screen 'Customers' shows the model res.partner in list, form view.
         Key fields: Name, Email, Phone."
    """
    modes = ", ".join(view_modes) if view_modes else "default"
    sentence = ("Screen '%s' shows the model %s in %s view."
                % (menu_name, res_model, modes))

    # Pick a handful of representative *input* fields for a readable summary.
    labels = []
    for fname, meta in (fields_meta or {}).items():
        if fname.startswith("_") or fname in _SYSTEM_FIELDS:
            continue
        meta = meta or {}
        # Skip computed / readonly fields — they aren't user-facing inputs.
        if meta.get("compute") or meta.get("readonly"):
            continue
        label = meta.get("string") or fname
        labels.append(label)
        if len(labels) >= 6:
            break
    if labels:
        sentence += " Key fields: %s." % ", ".join(labels)
    return sentence


def compose_field_table_rows(fields_meta, field_comments=None):
    """Return a list of row dicts ready for templating.

    **Only form-input fields are included.**  Specifically, the following are
    excluded so the resulting table matches what a user actually sees on a
    "New record" form:

    * System / audit fields (id, create_uid, write_date, etc.)
    * Computed fields (``compute`` attribute is a non-empty string)
    * Purely read-only fields (``readonly=True`` in fields_get)
    * Chatter / activity mixin fields (message_*, activity_*)
    * Fields whose technical name starts with ``_``

    Each row merges runtime metadata (``fields_get``) with any inline comment
    recovered from the source code.
    """
    field_comments = field_comments or {}
    rows = []
    for fname, meta in sorted((fields_meta or {}).items()):
        # --- skip non-input fields ---
        if fname.startswith("_"):
            continue
        if fname in _SYSTEM_FIELDS:
            continue
        meta = meta or {}
        # A non-empty 'compute' string means this is a computed field.
        if meta.get("compute"):
            continue
        # Purely read-only fields are not user-fillable inputs.
        if meta.get("readonly"):
            continue

        rows.append({
            "name": fname,
            "label": meta.get("string") or fname,
            "type": meta.get("type") or "",
            "required": bool(meta.get("required")),
            "help": meta.get("help") or field_comments.get(fname, "") or "",
        })
    return rows


def llm_caption(image_bytes, context_text, backend=None):
    """Optional Vision-LLM caption hook.

    By default returns ``None`` so callers fall back to the deterministic
    caption. An integrator can pass a ``backend`` callable implementing
    ``backend(image_bytes, context_text) -> str``.
    """
    if backend is None:
        return None
    try:
        return backend(image_bytes, context_text)
    except Exception:  # pragma: no cover - defensive, never break a run
        return None
