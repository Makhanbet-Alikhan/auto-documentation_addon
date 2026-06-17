# -*- coding: utf-8 -*-
"""Source-code parsing service (ORM-aware wrapper).

Bridges the pure-python :mod:`..services.ast_extractor` with the Odoo runtime:
it resolves a module's on-disk path and walks every ``.py`` file, merging the
results into one documentation map for the whole module.
"""
import logging
import os

from odoo import api, models
from odoo.modules.module import get_module_path

from ..services import ast_extractor

_logger = logging.getLogger(__name__)


class DocSourceParser(models.AbstractModel):
    _name = "doc.source.parser"
    _description = "Auto Doc - Source Code Parser Service"

    @api.model
    def parse_module(self, module_name):
        """Parse all Python sources of ``module_name``.

        Returns a dict::

            {
                "module_docstring": <first non-empty module docstring>,
                "classes": {ClassName: doc, ...},
                "methods": {"Class.method": doc, ...},
                "field_comments": {"Class.field": comment, ...},
            }
        """
        path = get_module_path(module_name)
        merged = {
            "module_docstring": None,
            "classes": {},
            "methods": {},
            "field_comments": {},
        }
        if not path or not os.path.isdir(path):
            _logger.warning("Module path not found for %s", module_name)
            return merged

        for root, _dirs, files in os.walk(path):
            # Skip vendored / build artefacts that are not part of the addon.
            if "node_modules" in root or "__pycache__" in root:
                continue
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                file_path = os.path.join(root, filename)
                parsed = ast_extractor.extract_from_file(file_path)

                if parsed.module_docstring and not merged["module_docstring"]:
                    merged["module_docstring"] = parsed.module_docstring

                for cname, info in parsed.classes.items():
                    if info.get("doc"):
                        merged["classes"].setdefault(cname, info["doc"])
                for mkey, info in parsed.methods.items():
                    if info.get("doc"):
                        merged["methods"].setdefault(mkey, info["doc"])
                for fkey, info in parsed.fields.items():
                    if info.get("comment"):
                        merged["field_comments"].setdefault(fkey, info["comment"])

        return merged

    @api.model
    def field_comments_for_class(self, parsed, class_name):
        """Extract ``{field_name: comment}`` for one class from parsed data."""
        prefix = "%s." % class_name
        out = {}
        for fkey, comment in (parsed or {}).get("field_comments", {}).items():
            if fkey.startswith(prefix):
                out[fkey[len(prefix):]] = comment
        return out
