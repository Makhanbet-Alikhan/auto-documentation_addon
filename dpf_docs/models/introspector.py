# -*- coding: utf-8 -*-
"""ORM introspection service.

An ``AbstractModel`` (no table) that answers the question "what does this
module contain?" purely through the Odoo ORM:

* the menu tree owned by the module (``ir.ui.menu`` via ``ir.model.data``);
* the window action behind each menu (``ir.actions.act_window``);
* the models and their field metadata (``fields_get``).

Using ``ir.model.data`` to resolve ownership gives a precise module -> object
mapping and is far more reliable than parsing XML data files by hand.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class DocIntrospector(models.AbstractModel):
    _name = "doc.introspector"
    _description = "Auto Doc - ORM Introspection Service"

    # ------------------------------------------------------------------
    # Ownership resolution
    # ------------------------------------------------------------------
    @api.model
    def _records_of_module(self, module_name, model_name):
        """Return records of ``model_name`` declared by ``module_name``.

        Resolved through ``ir.model.data`` (the XML-id registry), which links
        every externally-defined record to the module that created it.
        """
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

        Each node carries enough information to (a) rebuild the tree via
        ``parent_id`` and (b) generate a screenshot task when it has an action.
        """
        menus = self._records_of_module(module_name, "ir.ui.menu")
        nodes = []
        for menu in menus:
            action = menu.action  # reference field -> ir.actions.*
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
            }
            nodes.append(node)
        # Stable ordering: parents before children, then by sequence.
        nodes.sort(key=lambda n: (n["parent_id"] or 0, n["sequence"], n["menu_id"]))
        return nodes

    # ------------------------------------------------------------------
    # Field metadata
    # ------------------------------------------------------------------
    @api.model
    def get_fields_meta(self, res_model):
        """Return ``fields_get`` metadata for a model, or empty dict.

        Requests ``readonly`` and ``compute`` attributes in addition to the
        base set so ``text_composer.compose_field_table_rows`` can filter out
        computed / readonly fields and show only form-input fields.

        Run as a high-privilege user (e.g. admin) so group-restricted fields
        are not silently hidden from the documentation.
        """
        if not res_model or res_model not in self.env:
            return {}
        try:
            return self.env[res_model].fields_get(
                attributes=[
                    "string",
                    "help",
                    "type",
                    "required",
                    "relation",
                    "readonly",   # <-- NEW: needed to skip pure read-only fields
                    "compute",    # <-- NEW: non-empty string means it is computed
                ]
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("fields_get failed for %s: %s", res_model, exc)
            return {}

    # ------------------------------------------------------------------
    # Models owned by the module
    # ------------------------------------------------------------------
    @api.model
    def get_module_models(self, module_name):
        """Return the technical model names defined by ``module_name``."""
        ir_models = self.env["ir.model"].search([])
        result = []
        for rec in ir_models:
            # ``modules`` is a comma-separated string of contributing modules.
            mods = (rec.modules or "").split(",")
            mods = [m.strip() for m in mods]
            if module_name in mods:
                result.append({
                    "model": rec.model,
                    "name": rec.name,
                    "transient": rec.transient,
                })
        return result
