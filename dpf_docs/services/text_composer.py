# -*- coding: utf-8 -*-
"""Compose human-readable Russian prose from collected metadata.

Pure-python, side-effect free.  Generates user-oriented text.

Key design decisions
--------------------
* System fields (id, create_date, message_*, activity_*) are EXCLUDED from
  every user-facing section.
* Computed and readonly fields are excluded from the editable field table.
* Business-logic (workflow states, action buttons, constraints) gets its own
  dedicated section so users understand what the system does automatically.
* The primary model drives the module description.

v2 improvements
---------------
* compose_business_logic_section — now renders an ASCII workflow diagram
  (e.g. [Draft] → [Confirmed] → [Done]) and a "Validation rules" subsection
  derived from @api.constrains discoveries in the introspector.
* compose_integrations_section — new function that turns the
  integrations_json blob into readable prose for the Word export.
* compose_embedded_models_section — new function that renders One2many
  child models (embedded tabular parts) as a short description block.
"""
import logging

_logger = logging.getLogger(__name__)

_SYSTEM_FIELDS: frozenset = frozenset({
    "id", "display_name",
    "create_uid", "create_date", "write_uid", "write_date", "__last_update",
    "message_ids", "message_follower_ids", "message_partner_ids",
    "message_is_follower", "message_unread_counter", "message_attachment_count",
    "message_has_error", "message_has_error_counter",
    "message_needaction", "message_needaction_counter",
    "message_main_attachment_id",
    "activity_ids", "activity_state", "activity_user_id", "activity_type_id",
    "activity_type_icon", "activity_date_deadline", "my_activity_date_deadline",
    "activity_summary", "activity_exception_decoration", "activity_exception_icon",
    "activity_count",
    "website_message_ids", "has_message",
    "message_has_sms_error",
    "rating_ids", "rating_last_value", "rating_avg",
    "sequence",
    # website.published.mixin
    "website_published", "is_published", "website_url",
    "cover_properties", "header_visible", "footer_visible",
    "can_publish", "website_id", "website_description",
    # standard technical
    "active", "color", "priority",
    "access_token", "access_warning",
})

_TYPE_LABELS: dict = {
    "char":      "текст",
    "text":      "многострочный текст",
    "html":      "текст с форматированием",
    "integer":   "целое число",
    "float":     "число",
    "monetary":  "денежная сумма",
    "boolean":   "да / нет",
    "date":      "дата",
    "datetime":  "дата и время",
    "selection": "выбор из списка",
    "many2one":  "связанная запись",
    "many2many": "несколько связанных записей",
    "one2many":  "список записей",
    "binary":    "файл",
    "image":     "изображение",
    "reference": "ссылка",
    "json":      "данные",
}


def compose_module_description(manifest, main_model_doc):
    """Составить описание модуля верхнего уровня для конечных пользователей."""
    parts = []
    summary = (manifest or {}).get("summary", "").strip()
    if summary:
        parts.append(summary)
    description = (manifest or {}).get("description", "").strip()
    if description and len(description) > 20 and description != summary:
        parts.append(description)
    if main_model_doc:
        doc = main_model_doc.strip()
        if doc and doc not in parts:
            parts.append(doc)
    if not parts:
        return "Описание модуля недоступно."
    return "\n\n".join(parts)


def compose_model_description(model_name, class_doc, field_comments):
    """Составить короткий абзац описания модели из её docstring."""
    lines = []
    if class_doc:
        lines.append(class_doc.strip())
    else:
        lines.append("Объект системы: %s." % model_name)
    if field_comments:
        annotated = [c for c in field_comments.values() if c]
        if annotated:
            lines.append("Дополнительные описания: %d поле(й)." % len(annotated))
    return "\n\n".join(lines)


def compose_menu_caption(menu_name, res_model, view_modes, fields_meta, groups=None):
    """Описать экран для пользователя.

    Only includes USER-INPUT fields in the key-fields summary.
    Appends access groups when available.
    """
    view_labels = {
        "list":     "список",
        "form":     "форма",
        "kanban":   "канбан",
        "calendar": "календарь",
        "pivot":    "сводная таблица",
        "graph":    "график",
        "activity": "активности",
    }
    modes_ru = [view_labels.get(m.strip(), m.strip()) for m in (view_modes or [])]
    if modes_ru:
        mode_str = " и ".join(modes_ru) if len(modes_ru) <= 2 else ", ".join(modes_ru)
        sentence = "Экран «%s» отображает записи в режиме %s." % (menu_name, mode_str)
    else:
        sentence = "Экран «%s»." % menu_name

    input_labels = []
    for fname, meta in (fields_meta or {}).items():
        if fname.startswith("_") or fname in _SYSTEM_FIELDS:
            continue
        meta = meta or {}
        if meta.get("compute") or meta.get("readonly"):
            continue
        label = meta.get("string") or fname
        input_labels.append(label)
        if len(input_labels) >= 5:
            break

    if input_labels:
        sentence += " Основные поля: %s." % ", ".join(input_labels)

    if groups:
        clean_groups = []
        for g in groups:
            parts = g.split("/")
            clean_groups.append(parts[-1].strip())
        if clean_groups:
            sentence += " Доступен для: %s." % ", ".join(clean_groups)

    return sentence


def compose_field_table_rows(fields_meta, field_comments=None):
    """Вернуть строки для таблицы редактируемых полей.

    USER-INPUT ONLY: excludes system, computed, and readonly fields.
    Rows are returned unsorted; caller should sort required-first.
    """
    field_comments = field_comments or {}
    rows = []
    for fname, meta in sorted((fields_meta or {}).items()):
        if fname.startswith("_") or fname in _SYSTEM_FIELDS:
            continue
        meta = meta or {}
        if meta.get("compute") or meta.get("readonly"):
            continue

        ftype = meta.get("type") or ""
        type_label = _TYPE_LABELS.get(ftype, ftype)
        help_text = meta.get("help") or field_comments.get(fname, "") or ""
        if ftype == "selection" and meta.get("selection"):
            options = ", ".join(str(v[1]) for v in meta["selection"])
            if options and options not in help_text:
                help_text = ("%s  Варианты: %s." % (help_text, options)).strip()

        rows.append({
            "name": fname,
            "label": meta.get("string") or fname,
            "type": type_label,
            "required": bool(meta.get("required")),
            "help": help_text,
        })
    return rows


def compose_business_logic_section(business_logic, module_name=""):
    """Составить читаемое описание автоматизированного поведения системы.

    Covers:
    * Workflow state transitions (with ASCII diagram when <= 7 states)
    * Action buttons available on the form
    * Validation constraints (@api.constrains) shown as user-facing rules
    """
    lines = []

    # --- Workflow states ---
    states = business_logic.get("workflow_states") or []
    if states:
        lines.append("Статусы записи:")
        if len(states) <= 7:
            # ASCII diagram: [Draft] → [Confirmed] → [Done]
            parts = ["[%s]" % label for val, label in states]
            lines.append("  " + " → ".join(parts))
        else:
            # Long list: bullet per state
            for val, label in states:
                lines.append("  • %s" % label)
        lines.append("")

    # --- Action buttons ---
    actions = business_logic.get("action_buttons") or []
    if actions:
        lines.append("Кнопки действий:")
        lines.append(
            "На форме записи доступны следующие действия: %s."
            % ", ".join("«%s»" % a for a in actions[:10])
        )
        lines.append("")

    # --- Validation constraints ---
    constraints = business_logic.get("constraints") or []
    if constraints:
        lines.append("Автоматические проверки системы:")
        lines.append(
            "При сохранении записи система автоматически проверяет следующее:"
        )
        for c in constraints:
            fields_str = ", ".join(
                "«%s»" % f for f in c.get("fields", [])
            )
            label = c.get("label", c.get("method", ""))
            if fields_str:
                lines.append("  • %s (поля: %s)." % (label, fields_str))
            else:
                lines.append("  • %s." % label)
        lines.append("")

    return "\n".join(lines).strip()


def compose_integrations_section(integrations):
    """Составить текст раздела внешних интеграций для документа.

    ``integrations`` is the list produced by
    ``DocGeneration._build_service_integrations`` and stored in
    ``doc.module.integrations_json``.  Each item:

        {
            "file": "minio_service.py",
            "subdir": "services",
            "doc": "<module docstring>",
            "types": ["MinIO / S3", "HTTP POST"],
            "classes": ["MinioService"],
        }

    Returns plain text (no Markdown) suitable for insertion into a Word
    paragraph or a Markdown block.
    """
    if not integrations:
        return ""

    lines = [
        "Модуль взаимодействует со следующими внешними системами и сервисами:",
        "",
    ]
    for item in integrations:
        file_name = item.get("file", "")
        types = item.get("types") or []
        doc = (item.get("doc") or "").strip()
        subdir = item.get("subdir", "")

        type_str = ", ".join(types) if types else "сервис"
        header = "• %s (%s/%s)" % (type_str, subdir, file_name)
        lines.append(header)
        if doc:
            # Show only first sentence of the docstring (up to 200 chars)
            first_sentence = doc.split("\n")[0][:200]
            lines.append("  %s" % first_sentence)
        lines.append("")

    return "\n".join(lines).strip()


def compose_embedded_models_section(embedded_models):
    """Описать встроенные табличные части формы (One2many-дочерние модели).

    ``embedded_models`` is the list returned by
    ``DocIntrospector.get_embedded_models``.  Each item:

        {
            "field": "schedule_line_ids",
            "field_label": "Расписание",
            "model": "some.schedule.line",
            "name": "Schedule Lines",
        }
    """
    if not embedded_models:
        return ""

    lines = ["Форма содержит следующие встроенные таблицы:", ""]
    for item in embedded_models:
        label = item.get("field_label") or item.get("field", "")
        model = item.get("model", "")
        name = item.get("name", "")
        display = name if name and name != model else model
        lines.append("• вкладка «%s» (%s)" % (label, display))
    lines.append("")
    return "\n".join(lines).strip()


def llm_caption(image_bytes, context_text, backend=None):
    """Optional Vision-LLM caption hook.  Returns None when no backend set."""
    if backend is None:
        return None
    try:
        return backend(image_bytes, context_text)
    except Exception:
        return None
