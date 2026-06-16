# -*- coding: utf-8 -*-
{
    "name": "DPF Docs - Module Documentation Generator",
    "version": "19.0.1.1.0",
    "category": "Tools/Documentation",
    "summary": "Automatically generate module documentation: description, "
               "menu tree, per-menu screenshots and field tables.",
    "description": """
Auto Documentation Generator
============================

Generates technical/user documentation for any installed Odoo module:

* Module description (from manifest + main model docstrings).
* Full menu tree (from ir.ui.menu hierarchy).
* Per-menu screenshots captured automatically by an in-process headless
  browser (Playwright Python), or uploaded manually as a fallback.
* Per-menu description built from model metadata (and optional Vision LLM).
* Field tables (string, help, type, required) via fields_get().
* Texts extracted from Python docstrings AND inline comments (AST + tokenize).
* Project task enrichment via offline snapshots — works even after the source
  project is deleted from the Project module.

Output formats: QWeb PDF, standalone HTML, Markdown.

Architecture
------------
* Python (this addon) introspects the ORM and parses source code, producing a
  JSON "doc spec" plus screenshot tasks.
* Project enrichment: tasks are imported into doc.project.task.snapshot records
  once, then all enrichment reads from snapshots — no live dependency on the
  Project module after import.
* An in-process screenshot capturer (models/doc_screenshot_capturer.py) drives
  a headless Chromium via the Playwright Python library: it logs into this very
  Odoo, navigates each documented screen's action URL, and stores the PNG on
  the matching menu -- so every image is bound to its own screen.
* A standalone Node + Playwright worker (see /worker) remains available as an
  alternative for out-of-process / CI capture.
* A renderer merges texts + screenshots into the final documents.
    """,
    "author": "Alikhan",
    "website": "https://github.com/",
    "license": "LGPL-3",
    "depends": ["base", "web", "mail"],
    "data": [
        "security/doc_security.xml",
        "security/ir.model.access.csv",
        "data/doc_config_params.xml",
        "data/doc_cron.xml",
        # Reports first
        "report/doc_report.xml",
        "report/doc_report_action.xml",
        "views/doc_generation_views.xml",
        "views/doc_project_picker_wizard_views.xml",
        "views/doc_project_task_snapshot_views.xml",
        "views/doc_module_views.xml",
        "views/doc_menu_views.xml",
        "views/doc_menu_root.xml",
    ],
    "assets": {},
    "installable": True,
    "application": True,
    "auto_install": False,
}
