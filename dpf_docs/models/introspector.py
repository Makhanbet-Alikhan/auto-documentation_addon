# -*- coding: utf-8 -*-
"""ORM introspection service — Odoo AbstractModel."""
import logging
import re

from odoo import api, models

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
})


def _is_system_field(fname: str, meta: dict) -> bool:
    if fname.startswith("_"):
        return True
    return fname in _SYSTEM_FIELDS


def _is_user_input(fname: str, meta: dict) -> bool:
    if _is_system_field(fname, meta):
        return False
    if meta.get("compute"):
        return False
    if meta.get("readonly"):
        return False
    return True


class DocIntrospector(models.AbstractModel):
    _name = "doc.introspector"
    _description = "Auto Doc - ORM Introspection Service"

    @api.model
    def _records_of_module(self, module_name, model_name):
        imd = self.env["ir.model.data"].search([
            ("module", "=", module_name),
            ("model", "=", model_name),
        ])
        res_ids = imd.mapped("res_id")
        if not res_ids:
            return self.env[model_name].browse()
        return self.env[model_name].browse(res_ids).exists()

    @api.model
    def get_menu_tree(self, module_name):
        """Return the module's menus as a flat list of node dicts."""
        menus = self._records_of_module(module_name, "ir.ui.menu")
        nodes = []
        for menu in menus:
            action = menu.action
            group_names = []

            # Odoo 19: field is group_ids on ir.ui.menu
            menu_groups = getattr(menu, "group_ids", False) or getattr(menu, "groups_id", False) or []
            for g in menu_groups:
                group_names.append(g.full_name or g.name or str(g.id))

            if action:
                action_groups = getattr(action, "group_ids", False) or getattr(action, "groups_id", False) or []
                for g in action_groups:
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

    @api.model
    def get_fields_meta(self, res_model):
        if not res_model or res_model not in self.env:
            return {}
        try:
            return self.env[res_model].fields_get(
                attributes=[
                    "string", "help", "type", "required",
                    "relation", "readonly", "compute", "selection",
                ]
            )
        except Exception as exc:
            _logger.warning("fields_get failed for %s: %s", res_model, exc)
            return {}

    @api.model
    def get_user_input_fields(self, res_model):
        all_meta = self.get_fields_meta(res_model)
        return {
            fname: meta
            for fname, meta in all_meta.items()
            if _is_user_input(fname, meta or {})
        }

    @api.model
    def get_display_fields(self, res_model):
        all_meta = self.get_fields_meta(res_model)
        return {
            fname: meta
            for fname, meta in all_meta.items()
            if not _is_system_field(fname, meta or {})
            and not _is_user_input(fname, meta or {})
        }

    @api.model
    def get_business_logic(self, res_model):
        result = {"workflow_states": [], "action_buttons": []}
        if not res_model or res_model not in self.env:
            return result

        all_meta = self.get_fields_meta(res_model)
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
                        stages = self.env[relation].search([], order="sequence asc", limit=20)
                        result["workflow_states"] = [(str(s.id), s.name) for s in stages]
                except Exception:
                    pass
                break

        try:
            model_cls = type(self.env[res_model])
            for attr_name in sorted(dir(model_cls)):
                if attr_name.startswith("action_") and callable(getattr(model_cls, attr_name, None)):
                    label = re.sub(r"[_]+", " ", attr_name[len("action_"):]).strip().title()
                    if label and label not in result["action_buttons"]:
                        result["action_buttons"].append(label)
        except Exception as exc:
            _logger.debug("action_* discovery failed for %s: %s", res_model, exc)

        return result

    @api.model
    def get_module_models(self, module_name):
        imd = self.env["ir.model.data"].search([
            ("module", "=", module_name),
            ("model", "=", "ir.model"),
        ])
        model_ids = imd.mapped("res_id")
        if not model_ids:
            return []
        ir_models = self.env["ir.model"].browse(model_ids).exists()
        return [{"model": m.model, "name": m.name, "transient": m.transient} for m in ir_models]

    @api.model
    def get_primary_model(self, module_name):
        models_info = self.get_module_models(module_name)
        if not models_info:
            return None
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
                meta = self.env[res_model].fields_get(attributes=["type", "relation"])
            except Exception:
                continue
            for fmeta in meta.values():
                if fmeta.get("type") == "many2one":
                    rel = fmeta.get("relation")
                    if rel and rel in fan_in:
                        fan_in[rel] += 1

        primary = max(fan_in, key=fan_in.get)
        return primary if fan_in[primary] > 0 else sorted(model_names)[0]
