# -*- coding: utf-8 -*-
"""Compose human-readable Russian prose from collected metadata.

Pure-python, side-effect free.  Generates user-oriented text.

Key design decisions
--------------------
* System fields (id, create_date, message_*, activity_*) are EXCLUDED from
  every user-facing section.
* Website/SEO/portal fields injected by Odoo mixins are also excluded.
* Field filtering is centralised in model_doc_utils.is_user_visible_candidate
  — no local _SYSTEM_FIELDS copy in this module.
* Computed and readonly fields are excluded from the editable field table.
* Business-logic (workflow states, action buttons, constraints) gets its own
  dedicated section so users understand what the system does automatically.
* The primary model drives the module description.

v3 fixes applied
----------------
* PROBLEM 1: compose_inheritance_section() — architecture overview section.
* PROBLEM 4: compose_business_logic_section() uses ValidationError messages
  from ast_extractor instead of raw method names for constraints.
* PROBLEM 5: sanitize_task_text() strips internal TZ/TS blocks from tasks;
  compose_related_tasks_section() places tasks in a dedicated section.
* PROBLEM 6: compose_inherited_model_section() generates a single compact
  section for _inherit models listing only addon-contributed fields.
* PROBLEM 7: compose_module_description() falls back to model._description.
"""
import logging
import re

from .model_doc_utils import is_user_visible_candidate

_logger = logging.getLogger(__name__)

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

# ---------------------------------------------------------------------------
# PROBLEM 5 — Task text sanitization
# ---------------------------------------------------------------------------

# Patterns that mark internal TZ/TS content blocks to strip
_INTERNAL_BLOCK_PATTERNS = [
    re.compile(
        r"(ЧТО СДЕЛАТЬ|ОПИСАНИЕ\s*\(из\s*ТС\)|ЧТО\s+СДЕЛАТЬ)[:\s]*.*?(?=\n\n|\Z)",
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(r"Оценка:\s*\d+[^\n]*\n?", re.IGNORECASE),
    re.compile(r"API:[^\n]*\n?", re.IGNORECASE),
    re.compile(r"[-–—]{3,}\s*\n"),  # horizontal rule separators
]

# Tags that mark a task as internal — do not include in user docs
_INTERNAL_TAGS = frozenset({
    "internal", "technical", "tech", "tz", "тз", "тс",
    "internal-only", "dev-only",
})


def sanitize_task_text(text):
    """Remove internal TZ/TS blocks from a project task body.

    Strips patterns like 'ЧТО СДЕЛАТЬ:', 'ОПИСАНИЕ (из ТС)',
    'Оценка: X нед.', 'API:' that are intended for developers only.

    PROBLEM 5 fix.
    """
    if not text:
        return ""
    result = text
    for pattern in _INTERNAL_BLOCK_PATTERNS:
        result = pattern.sub("", result)
    # Collapse multiple blank lines into one
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def is_internal_task(task_tags):
    """Return True if the task carries any internal/technical tag.

    ``task_tags`` should be a list/set of tag name strings (lowercase).

    PROBLEM 5 fix.
    """
    if not task_tags:
        return False
    return bool(_INTERNAL_TAGS & {t.lower().strip() for t in task_tags})


def compose_related_tasks_section(tasks):
    """Compose a dedicated 'Связанные задачи' section from project tasks.

    Each task dict should have keys: ``name``, ``description``, ``tags``.
    Tasks tagged as internal are excluded entirely.
    Task bodies are sanitized before inclusion.

    PROBLEM 5 fix: tasks go into their own section, never inside function
    bodies.
    """
    if not tasks:
        return ""

    lines = ["Связанные задачи", "="* 40, ""]
    included = 0
    for task in tasks:
        tags = task.get("tags") or []
        if is_internal_task(tags):
            continue
        name = (task.get("name") or "").strip()
        description = sanitize_task_text(task.get("description") or "")
        if not name:
            continue
        lines.append("• %s" % name)
        if description:
            for line in description.splitlines()[:5]:  # first 5 lines
                lines.append("  %s" % line)
        lines.append("")
        included += 1

    if not included:
        return ""
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# PROBLEM 1 — Architecture / inheritance section
# ---------------------------------------------------------------------------

def compose_inheritance_section(models_info):
    """Compose the 'Архитектура модуля' section.

    ``models_info`` is the list returned by
    ``DocIntrospector.get_module_models()`` — each entry has keys
    ``model``, ``name``, ``inherited`` (bool), ``transient`` (bool).

    The section is split into two subsections:
    * 'Собственные модели'   — entries with ``inherited=False``
    * 'Расширяемые модели Odoo' — entries with ``inherited=True``

    PROBLEM 1 fix.
    """
    if not models_info:
        return ""

    own = [m for m in models_info if not m.get("inherited")]
    inherited = [m for m in models_info if m.get("inherited")]

    lines = ["Архитектура модуля", "=" * 40, ""]

    if own:
        lines.append("Собственные модели")
        lines.append("-" * 30)
        lines.append(
            "Следующие модели объявлены непосредственно в данном модуле "
            "(атрибут _name):"
        )
        for m in own:
            transient_mark = " (временная модель)" if m.get("transient") else ""
            lines.append(
                "  • %s — %s%s" % (m["model"], m.get("name") or m["model"], transient_mark)
            )
        lines.append("")

    if inherited:
        lines.append("Расширяемые модели Odoo")
        lines.append("-" * 30)
        lines.append(
            "Следующие стандартные модели Odoo расширяются данным модулем "
            "(атрибут _inherit) — к ним добавляются новые поля и/или поведение:"
        )
        for m in inherited:
            lines.append(
                "  • %s — %s" % (m["model"], m.get("name") or m["model"])
            )
        lines.append("")

    if not own and not inherited:
        return ""

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# PROBLEM 6 — Inherited model single-function section
# ---------------------------------------------------------------------------

def compose_inherited_model_section(model_name, model_display_name, addon_fields_meta):
    """Compose a compact section for an _inherit model.

    Instead of generating full 'Просмотр' + 'Создание' duplicate functions,
    we emit a single section: 'Работа с записью «ModelName»' that lists only
    the fields contributed by the addon (not all base model fields).

    ``addon_fields_meta`` is the dict returned by
    ``DocIntrospector.get_addon_own_fields()``.

    PROBLEM 6 fix.
    """
    display = model_display_name or model_name
    lines = [
        "Работа с записью «%s»" % display,
        "-" * 40,
        "Модуль добавляет следующие дополнительные поля к стандартной форме Odoo:",
        "",
    ]

    if not addon_fields_meta:
        lines.append("(Модуль не добавляет собственных полей к данной модели.)")
    else:
        for fname, meta in sorted((addon_fields_meta or {}).items()):
            meta = meta or {}
            label = meta.get("string") or fname
            ftype = meta.get("type") or ""
            type_label = _TYPE_LABELS.get(ftype, ftype)
            required_mark = " *" if meta.get("required") else ""
            help_text = (meta.get("help") or "").strip()
            help_part = " — %s" % help_text if help_text else ""
            lines.append(
                "  • %s%s (%s)%s" % (label, required_mark, type_label, help_part)
            )

    lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Core composers (existing, with targeted fixes)
# ---------------------------------------------------------------------------

def compose_module_description(manifest, main_model_doc, models_info=None, env=None):
    """Составить описание модуля верхнего уровня для конечных пользователей.

    PROBLEM 7 fix: when summary/description are absent or too short, a
    fallback paragraph is built from the module's own model ``_description``
    attributes so the section is never empty.

    ``models_info`` — list from DocIntrospector.get_module_models().
    ``env``         — Odoo environment (needed to read _description).
    """
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

    # PROBLEM 7 fallback: generate from own model _descriptions
    if not parts and models_info and env is not None:
        own_models = [m for m in models_info if not m.get("inherited")]
        generated_lines = []
        for minfo in own_models:
            model_key = minfo["model"]
            if model_key not in env:
                continue
            model_obj = env[model_key]
            model_description = getattr(model_obj, "_description", None) or ""
            model_name_label = minfo.get("name") or model_key
            if model_description and model_description != model_key:
                generated_lines.append(
                    "• %s (%s): %s" % (model_name_label, model_key, model_description)
                )
            else:
                generated_lines.append(
                    "• %s (%s)" % (model_name_label, model_key)
                )
        if generated_lines:
            parts.append(
                "Модуль предоставляет следующие объекты системы:\n"
                + "\n".join(generated_lines)
            )

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
    """Описать экран для пользователя."""
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
        meta = meta or {}
        field_info = dict(meta)
        field_info["name"] = fname
        if not is_user_visible_candidate(field_info):
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
    """Вернуть строки для таблицы редактируемых полей."""
    field_comments = field_comments or {}
    rows = []
    for fname, meta in sorted((fields_meta or {}).items()):
        meta = meta or {}
        field_info = dict(meta)
        field_info["name"] = fname
        if not is_user_visible_candidate(field_info):
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


def compose_business_logic_section(business_logic, module_name="",
                                   validation_errors=None):
    """Составить читаемое описание автоматизированного поведения системы.

    PROBLEM 4 fix: ``validation_errors`` is a dict {method_name: message}
    from ast_extractor.  When available, the human-readable ValidationError
    message replaces the raw method name in the constraints list.
    """
    validation_errors = validation_errors or {}
    lines = []

    # --- Workflow states ---
    states = business_logic.get("workflow_states") or []
    if states:
        lines.append("Статусы записи:")
        if len(states) <= 7:
            parts = ["[%s]" % label for val, label in states]
            lines.append("  " + " → ".join(parts))
        else:
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

    # --- Validation constraints (PROBLEM 4 fix) ---
    constraints = business_logic.get("constraints") or []
    if constraints:
        lines.append("Автоматические проверки системы:")
        lines.append(
            "При сохранении записи система автоматически проверяет следующее:"
        )
        for c in constraints:
            method_name = c.get("method", "")
            fields_str = ", ".join(
                "«%s»" % f for f in c.get("fields", [])
            )
            # Prefer ValidationError message; fall back to humanised method name
            error_msg = (
                c.get("error_message")
                or validation_errors.get(method_name)
                or ""
            ).strip()
            if error_msg:
                # Show the actual user-facing error message
                display_label = error_msg
            else:
                display_label = c.get("label", method_name)

            if fields_str:
                lines.append(
                    "  • %s (поля: %s)." % (display_label, fields_str)
                )
            else:
                lines.append("  • %s." % display_label)
        lines.append("")

    return "\n".join(lines).strip()


def compose_integrations_section(integrations):
    """Составить текст раздела внешних интеграций для документа."""
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
            first_sentence = doc.split("\n")[0][:200]
            lines.append("  %s" % first_sentence)
        lines.append("")

    return "\n".join(lines).strip()


def compose_embedded_models_section(embedded_models):
    """Описать встроенные табличные части формы (One2many-дочерние модели)."""
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
