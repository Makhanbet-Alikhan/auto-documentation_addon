# -*- coding: utf-8 -*-
"""Compose human-readable prose from collected metadata.

Pure-python and side-effect free. Two layers:

* deterministic composers that turn structured metadata into readable text
  with no external dependency (the default, free, reproducible path);
* a thin ``llm_caption`` hook that an integrator can wire to any multimodal /
  text model. It is optional and never required for the module to work.
"""


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

    # Pick a handful of representative fields for a readable summary.
    labels = []
    for fname, meta in (fields_meta or {}).items():
        if fname.startswith("_") or fname in ("id", "display_name"):
            continue
        label = (meta or {}).get("string") or fname
        labels.append(label)
        if len(labels) >= 6:
            break
    if labels:
        sentence += " Key fields: %s." % ", ".join(labels)
    return sentence


def compose_field_table_rows(fields_meta, field_comments=None):
    """Return a list of row dicts ready for templating.

    Each row merges runtime metadata (``fields_get``) with any inline comment
    recovered from the source code.
    """
    field_comments = field_comments or {}
    rows = []
    for fname, meta in sorted((fields_meta or {}).items()):
        meta = meta or {}
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
