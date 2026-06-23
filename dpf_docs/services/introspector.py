# -*- coding: utf-8 -*-
"""ORM introspection service.

Provides three categories of field information for the documentation generator:

1. USER_INPUT   — fields the user fills in on a form (shown in the field table)
2. COMPUTED_RO  — computed/readonly fields shown on screen but not fillable
3. SYSTEM       — internal Odoo plumbing; completely excluded from docs

The distinction is critical for user-oriented documentation:
  * Field table  → USER_INPUT only
  * Menu caption → USER_INPUT only (key fields summary)
  * Business-logic section → derived from @api.constrains / action_* methods
    extracted by the AST extractor, not from fields_get

Access-group information is also collected from ir.ui.menu records so the
document can state which user roles can see each screen.

v2 improvements
---------------
* get_module_models — now also returns models extended via _inherit by
  scanning ir.model.fields records owned by the module.  This covers
  modules that add fields to existing models without declaring a new model.
* get_business_logic — now also collects @api.constrains methods so
  validation rules (conflict checks, date validations, etc.) are visible
  in the generated documentation.
* get_embedded_models — new method that discovers One2many child models
  of any given model ("tabular parts" embedded in forms).
* _SYSTEM_FIELDS — extended with website.published.mixin fields so they
  are not shown in user-facing field tables.

v3 fixes
--------
* PROBLEM 2: get_fields_meta() now checks getattr(model_cls, fname) for
  a .compute attribute to correctly flag store=True computed fields.
* PROBLEM 3: get_business_logic() filters action_* methods to only those
  defined directly in the addon's own classes (via __qualname__ check).
"""
import logging
import re

from odoo import api, models

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field exclusion sets
# ---------------------------------------------------------------------------

# Always-present Odoo system fields — never shown to users in docs
_SYSTEM_FIELDS: frozenset = frozenset({
    "id", "display_name",
    "create_uid", "create_date", "write_uid", "write_date", "__last_update",
    # Chatter / mail.thread
    "message_ids", "message_follower_ids", "message_partner_ids",
    "message_is_follower", "message_unread_counter", "message_attachment_count",
    "message_has_error", "message_has_error_counter",
    "message_needaction", "message_needaction_counter",
    "message_main_attachment_id",
    # Activity mixin
    "activity_ids", "activity_state", "activity_user_id", "activity_type_id",
    "activity_type_icon", "activity_date_deadline", "my_activity_date_deadline",
    "activity_summary", "activity_exception_decoration", "activity_exception_icon",
    "activity_count",
    # Website / portal
    "website_message_ids", "has_message",
    # SMS
    "message_has_sms_error",
    # Rating
    "rating_ids", "rating_last_value", "rating_avg",
    # Sequence (internal ordering)
    "sequence",
    # website.published.mixin — never relevant for user-facing docs
    "website_published", "is_published", "website_url",
    "cover_properties", "header_visible", "footer_visible",
    "can_publish", "website_id", "website_description",
    # Standard technical fields added by various mixins
    "active", "color", "priority",
    "access_token", "access_warning",
})


def _is_system_field(fname: str, meta: dict) -> bool:
    """Return True when the field should be completely excluded from docs."""
    if fname.startswith("_"):
        return True
    return fname in _SYSTEM_FIELDS


def _is_user_input(fname: str, meta: dict) -> bool:
    """Return True when the field is editable by the user on a form."""
    if _is_system_field(fname, meta):
        return False
    if meta.get("compute"):
        return False
    if meta.get("readonly"):
        return False
    return True


# ---------------------------------------------------------------------------
# Helper: collect class names defined directly in the addon's source files
# ---------------------------------------------------------------------------

def _get_addon_class_names(module_name):
    """Return the set of Python class names defined in the addon's source.

    Uses sys.modules to find the already-loaded classes whose __module__
    path contains the addon's technical name.  This gives us a reliable
    set for __qualname__ filtering in get_business_logic().
    """
    import sys
    class_names = set()
    prefix = "%s." % module_name  # e.g. "dpf_events."
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not (mod_name == module_name or mod_name.startswith(prefix)):
            continue
        try:
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name, None)
                if obj is None:
                    continue
                if isinstance(obj, type):
                    class_names.add(obj.__name__)
        except Exception:
            pass
    return class_names


class DocIntrospector(models.AbstractModel):
    _name = "doc.introspector"
    _description = "Auto Doc - ORM Introspection Service"

    # ------------------------------------------------------------------
    # Ownership resolution
    # ------------------------------------------------------------------
    @api.model
    def _records_of_module(self, module_name, model_name):
        """Return records of ``model_name`` declared by ``module_name``."""
        imd = self.env["ir.model.data"].search([
            ("module", "=", module_name),
            ("model", "=", model_name),
        ])
        res_ids = imd.mapped("res_id")
        if not res_ids:
            return self.env[model_name].browse()
        return self.env[model_name].browse(res_ids).exists()

    # ------------------------------------------------------------------
    # Menu tree
    # ------------------------------------------------------------------
    @api.model
    def get_menu_tree(self, module_name):
        """Return the module's menus as a flat list of node dicts.

        Each node carries ``groups`` — a list of group names so the
        documentation can state which user roles can access the screen.
        """
        menus = self._records_of_module(module_name, "ir.ui.menu")
        nodes = []
        for menu in menus:
            action = menu.action
            group_names = []
            for g in menu.groups_id:
                group_names.append(g.full_name or g.name or str(g.id))
            if action and hasattr(action, "groups_id"):
                for g in action.groups_id:
                    full = g.full_name or g.name or str(g.id)
                    if full not in group_names:
                        group_names.append(full)

            node = {
                "menu_id": menu.id,
                "name": menu.name,
                "complete_name": menu.complete_name,
                "parent_id": menu.parent_id.id or False,
                "sequence": menu.sequence,
                "action_model": action._name if action else False,
                "action_id": action.id if action else False,
                "res_model": getattr(action, "res_model", False) if action else False,
                "view_modes": (
                    action.view_mode.split(",")
                    if action and getattr(action, "view_mode", False) else []
                ),
                "web_url": (
                    "/odoo/action-%s" % action.id
                    if action and action._name == "ir.actions.act_window" else False
                ),
                "groups": group_names,
            }
            nodes.append(node)
        nodes.sort(key=lambda n: (n["parent_id"] or 0, n["sequence"], n["menu_id"]))
        return nodes

    # ------------------------------------------------------------------
    # Field metadata
    # ------------------------------------------------------------------
    @api.model
    def get_fields_meta(self, res_model):
        """Return full fields_get metadata.

        FIX PROBLEM 2: For each field, if fields_get() did not return a
        'compute' key (can happen for store=True inherited computed fields),
        we additionally check getattr(model_cls, fname) for a .compute
        attribute and set computed=True in the result dict.
        """
        if not res_model or res_model not in self.env:
            return {}
        try:
            raw = self.env[res_model].fields_get(
                attributes=[
                    "string", "help", "type", "required",
                    "relation", "readonly", "compute", "selection",
                ]
            )
        except Exception as exc:
            _logger.warning("fields_get failed for %s: %s", res_model, exc)
            return {}

        # PROBLEM 2 fix: cross-check via class attribute for computed fields
        # that fields_get() may report without the 'compute' key (store=True)
        try:
            model_cls = type(self.env[res_model])
            for fname, meta in raw.items():
                if meta.get("compute"):
                    # Already flagged, skip
                    continue
                field_obj = getattr(model_cls, fname, None)
                if field_obj is not None and getattr(field_obj, "compute", None):
                    meta["compute"] = field_obj.compute
        except Exception as exc:
            _logger.debug(
                "Compute attribute fallback check failed for %s: %s", res_model, exc
            )

        return raw

    @api.model
    def get_user_input_fields(self, res_model):
        """Return only the fields a user fills in on the form.

        Excludes: system fields, computed fields, readonly fields.
        """
        all_meta = self.get_fields_meta(res_model)
        return {
            fname: meta
            for fname, meta in all_meta.items()
            if _is_user_input(fname, meta or {})
        }

    @api.model
    def get_display_fields(self, res_model):
        """Return computed/readonly fields that are visible on screen."""
        all_meta = self.get_fields_meta(res_model)
        return {
            fname: meta
            for fname, meta in all_meta.items()
            if not _is_system_field(fname, meta or {})
            and not _is_user_input(fname, meta or {})
        }

    # ------------------------------------------------------------------
    # Business logic extraction
    # ------------------------------------------------------------------
    @api.model
    def get_business_logic(self, res_model, module_name=None):
        """Extract business logic visible to the user from the ORM.

        Returns a dict:
          - ``workflow_states``: list of (value, label) from 'state' selection
          - ``action_buttons``: list of action button labels
          - ``constraints``:    list of {method, label, fields} dicts from
                                @api.constrains — shown as validation rules

        FIX PROBLEM 3: When ``module_name`` is provided, action_* methods
        are filtered to only those defined directly in the addon's own
        Python classes (checked via __qualname__ prefix).
        """
        result = {
            "workflow_states": [],
            "action_buttons": [],
            "constraints": [],
        }
        if not res_model or res_model not in self.env:
            return result

        all_meta = self.get_fields_meta(res_model)

        # --- Workflow states ---
        for fname in ("state", "kanban_state", "stage_id"):
            meta = all_meta.get(fname)
            if not meta:
                continue
            if meta.get("type") == "selection":
                result["workflow_states"] = meta.get("selection") or []
                break
            if meta.get("type") == "many2one" and "stage" in fname:
                try:
                    relation = meta.get("relation")
                    if relation and relation in self.env:
                        stages = self.env[relation].search(
                            [], order="sequence asc", limit=20
                        )
                        result["workflow_states"] = [
                            (str(s.id), s.name) for s in stages
                        ]
                except Exception:
                    pass
                break

        # --- Action buttons (PROBLEM 3 fix) ---
        # Build the set of class names belonging to the addon (if known).
        addon_class_names = set()
        if module_name:
            addon_class_names = _get_addon_class_names(module_name)

        try:
            model_cls = type(self.env[res_model])
            for attr_name in sorted(dir(model_cls)):
                if not attr_name.startswith("action_"):
                    continue
                method = getattr(model_cls, attr_name, None)
                if not callable(method):
                    continue

                # PROBLEM 3: filter to addon-own methods only
                if addon_class_names:
                    qualname = getattr(method, "__qualname__", "") or ""
                    # __qualname__ for a method is "ClassName.method_name"
                    owner_class = qualname.split(".")[0] if "." in qualname else ""
                    if owner_class and owner_class not in addon_class_names:
                        continue

                label = attr_name[len("action_"):]
                label = re.sub(r"[_]+", " ", label).strip().title()
                if label and label not in result["action_buttons"]:
                    result["action_buttons"].append(label)
        except Exception as exc:
            _logger.debug("action_* discovery failed for %s: %s", res_model, exc)

        # --- Constraints (@api.constrains) ---
        try:
            model_cls = type(self.env[res_model])
            for attr_name in sorted(dir(model_cls)):
                method = getattr(model_cls, attr_name, None)
                if not callable(method):
                    continue
                constrained_fields = getattr(method, "_constrains", None)
                if not constrained_fields:
                    continue

                # PROBLEM 3: same filter for constraints — own methods only
                if addon_class_names:
                    qualname = getattr(method, "__qualname__", "") or ""
                    owner_class = qualname.split(".")[0] if "." in qualname else ""
                    if owner_class and owner_class not in addon_class_names:
                        continue

                label = re.sub(r"[_]+", " ", attr_name).strip().title()
                result["constraints"].append({
                    "method": attr_name,
                    "label": label,
                    "fields": list(constrained_fields),
                    # ValidationError message is enriched later by ast_extractor
                    "error_message": "",
                })
        except Exception as exc:
            _logger.debug("constraints discovery failed for %s: %s", res_model, exc)

        return result

    # ------------------------------------------------------------------
    # Module models (own + inherited via _inherit)
    # ------------------------------------------------------------------
    @api.model
    def get_module_models(self, module_name):
        """Return list of model info dicts declared OR extended by the module.

        Two passes are performed:

        1. Own models — ir.model records whose external ID belongs to
           ``module_name``.  These are models with ``_name = ...`` in the
           module source.

        2. Inherited models — models to which ``module_name`` added at least
           one field (``_inherit`` pattern).  Detected by scanning
           ir.model.fields records whose external ID belongs to the module.

        Each entry in the returned list carries an ``inherited`` boolean so
        callers can render own and inherited models differently.
        """
        # Pass 1: own models
        imd_models = self.env["ir.model.data"].search([
            ("module", "=", module_name),
            ("model", "=", "ir.model"),
        ])
        model_ids = imd_models.mapped("res_id")
        ir_models_own = self.env["ir.model"].browse(model_ids).exists()

        result = []
        seen_models = set()

        for m in ir_models_own:
            result.append({
                "model": m.model,
                "name": m.name,
                "transient": m.transient,
                "inherited": False,
            })
            seen_models.add(m.model)

        # Pass 2: models extended via _inherit (the module added fields to them)
        imd_fields = self.env["ir.model.data"].search([
            ("module", "=", module_name),
            ("model", "=", "ir.model.fields"),
        ])
        if imd_fields:
            field_ids = imd_fields.mapped("res_id")
            ir_fields = self.env["ir.model.fields"].browse(field_ids).exists()
            for fld in ir_fields:
                m = fld.model_id
                if not m or m.model in seen_models:
                    continue
                result.append({
                    "model": m.model,
                    "name": m.name,
                    "transient": m.transient,
                    "inherited": True,
                })
                seen_models.add(m.model)

        return result

    # ------------------------------------------------------------------
    # Inherited-only fields for a model (PROBLEM 6 helper)
    # ------------------------------------------------------------------
    @api.model
    def get_addon_own_fields(self, res_model, module_name):
        """Return only the fields that ``module_name`` added to ``res_model``.

        Used for inherited models: instead of listing ALL fields of the base
        model, we document only what the addon contributes.

        Detection: fields whose ir.model.data external ID belongs to the module.
        Falls back to all user-input fields if no imd records are found.
        """
        imd_fields = self.env["ir.model.data"].search([
            ("module", "=", module_name),
            ("model", "=", "ir.model.fields"),
        ])
        if not imd_fields:
            return self.get_user_input_fields(res_model)

        field_ids = set(imd_fields.mapped("res_id"))
        ir_fields = self.env["ir.model.fields"].browse(list(field_ids)).exists()
        # Build set of field names owned by this module on this model
        owned_fnames = {
            fld.name
            for fld in ir_fields
            if fld.model_id and fld.model_id.model == res_model
        }
        if not owned_fnames:
            return self.get_user_input_fields(res_model)

        all_meta = self.get_fields_meta(res_model)
        return {
            fname: meta
            for fname, meta in all_meta.items()
            if fname in owned_fnames
        }

    # ------------------------------------------------------------------
    # Embedded (One2many child) models
    # ------------------------------------------------------------------
    @api.model
    def get_embedded_models(self, res_model):
        """Return One2many child models of ``res_model``.

        These are "tabular parts" embedded in the form view (e.g. schedule
        lines, order lines, registration rows) that have no top-level menu
        of their own but are still important for documentation.

        Returns a list of dicts::

            [
                {
                    "field": "schedule_line_ids",
                    "model": "some.schedule.line",
                    "name": "Schedule Lines",
                }
            ]
        """
        if not res_model or res_model not in self.env:
            return []
        try:
            meta = self.env[res_model].fields_get(
                attributes=["type", "relation", "string"]
            )
        except Exception as exc:
            _logger.warning("get_embedded_models fields_get failed for %s: %s", res_model, exc)
            return []

        embedded = []
        seen_relations = set()
        for fname, fmeta in meta.items():
            if fmeta.get("type") != "one2many":
                continue
            rel = fmeta.get("relation")
            if not rel or rel in seen_relations:
                continue
            if rel not in self.env:
                continue
            seen_relations.add(rel)
            model_obj = self.env[rel]
            embedded.append({
                "field": fname,
                "field_label": fmeta.get("string") or fname,
                "model": rel,
                "name": model_obj._description or rel,
            })
        return embedded

    # ------------------------------------------------------------------
    # Primary model detection
    # ------------------------------------------------------------------
    @api.model
    def get_primary_model(self, module_name):
        """Detect the primary (main) model of a module.

        Strategy: the model with the most Many2one fields pointing TO it
        from within the same module is the primary model.
        Falls back to the first non-transient model alphabetically.

        Note: only *own* (non-inherited) models are candidates for primary.
        """
        models_info = self.get_module_models(module_name)
        if not models_info:
            return None

        non_transient = [
            m for m in models_info
            if not m.get("transient") and not m.get("inherited")
        ]
        if not non_transient:
            non_transient = [m for m in models_info if not m.get("transient")]
        if not non_transient:
            return models_info[0]["model"]
        if len(non_transient) == 1:
            return non_transient[0]["model"]

        model_names = {m["model"] for m in non_transient}
        fan_in = {m: 0 for m in model_names}
        for minfo in non_transient:
            res_model = minfo["model"]
            if res_model not in self.env:
                continue
            try:
                meta = self.env[res_model].fields_get(
                    attributes=["type", "relation"]
                )
            except Exception:
                continue
            for fname, fmeta in meta.items():
                if fmeta.get("type") == "many2one":
                    rel = fmeta.get("relation")
                    if rel and rel in fan_in:
                        fan_in[rel] += 1

        primary = max(fan_in, key=fan_in.get)
        if fan_in[primary] == 0:
            return sorted(model_names)[0]
        return primary
