# -*- coding: utf-8 -*-
{
    "name": "DPF Docs - Module Documentation Generator",
    "version": "19.0.1.2.0",
    "category": "Tools/Documentation",
    "summary": "Automatically generate module documentation: description, "
               "menu tree, per-menu screenshots and field tables.",
    "description": """
Auto Documentation Generator
============================

Generates technical/user documentation for any installed Odoo module:

* Module description (from manifest + main model docstrings).
* Full menu tree (from ir.ui.menu hierarchy).
* Per-menu screenshots captured automatically by Playwright, or uploaded manually.
* Per-menu description built from model metadata.
* Field tables (string, help, type, required) via fields_get().
* Texts extracted from Python docstrings AND inline comments (AST + tokenize).
* Project task enrichment via offline snapshots.

Task enrichment
---------------
Tasks from a project.project are imported into doc.project.task.snapshot records.
Matching is by [module_tag] prefix — language-agnostic.
One doc.function is created per tagged subtask.

Global Snapshot Sets
--------------------
doc.project.snapshot.set — one shared download per project, reusable across
all documentation generation runs.  See Tools > Documentation > Project Snapshots.
""",
    "author": "DPF",
    "depends": ["base", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/doc_generation_views.xml",
        "views/doc_project_snapshot_set_views.xml",
        "views/doc_menu_items.xml",
    ],
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
