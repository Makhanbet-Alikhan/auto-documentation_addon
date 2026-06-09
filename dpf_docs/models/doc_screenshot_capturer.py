# -*- coding: utf-8 -*-
"""In-process screenshot capture service.

This service captures Odoo screen screenshots automatically. It drives a
headless Chromium that logs into *this* Odoo instance, navigates to each
documented screen's stable action URL, waits for the Owl view to render, and
screenshots the content area. Each PNG is stored on the matching ``doc.menu``
record and then synced onto its ``doc.function`` so the image always renders
directly beneath that screen's text in the manual.

Why a SUBPROCESS instead of calling Playwright inline
-----------------------------------------------------
Playwright's *sync* API spawns a Node driver and refuses to run inside a thread
that already owns an asyncio event loop. Odoo serves HTTP requests from worker
threads whose state is unpredictable, so calling ``sync_playwright()`` directly
from a button handler frequently fails with greenlet / event-loop errors and
silently produces no screenshots. To be robust we run the whole browser job in
a clean, isolated **child process** (``runner.py``): Odoo writes a small JSON
spec, launches the runner, and reads the captured PNG files back. The child has
its own pristine event loop, so Playwright works reliably regardless of how
Odoo is serving requests.

Graceful degradation
---------------------
If Playwright (or its Chromium browser) is not installed, the runner reports
that clearly and the capturer raises an actionable :class:`UserError`. The
manual-upload path keeps working untouched in all cases.
"""
import base64
import json
import logging
import os
import subprocess
import sys
import tempfile

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Path to the standalone runner script that performs the actual browser work
# in a separate process (see ``models/screenshot_runner.py``).
_RUNNER = os.path.join(os.path.dirname(__file__), "screenshot_runner.py")


class DocScreenshotCapturer(models.AbstractModel):
    _name = "doc.screenshot.capturer"
    _description = "Auto Doc - In-process Screenshot Capturer"

    # ------------------------------------------------------------------
    # Availability / configuration
    # ------------------------------------------------------------------
    @api.model
    def is_available(self):
        """Return ``True`` when Playwright can be imported in this environment."""
        try:
            import playwright  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    @api.model
    def _config(self):
        """Read capture settings from ``ir.config_parameter`` with defaults."""
        get = self.env["ir.config_parameter"].sudo().get_param
        # Default base URL: talk to the local Odoo over the configured HTTP port.
        port = self.env["ir.config_parameter"].sudo().get_param(
            "dpf_docs.http_port"
        ) or os.environ.get("PORT") or "8069"
        default_base = "http://localhost:%s" % port
        return {
            "base_url": get("dpf_docs.base_url") or default_base,
            "login": get("dpf_docs.capture_login") or "admin",
            "password": get("dpf_docs.capture_password") or "admin",
            "database": get("dpf_docs.capture_db") or self.env.cr.dbname,
            "ready_selector": get("dpf_docs.ready_selector") or ".o_content",
            "capture_selector": get("dpf_docs.capture_selector")
            or ".o_action_manager",
            "render_timeout": int(get("dpf_docs.render_timeout") or 30000),
            "viewport_width": int(get("dpf_docs.viewport_width") or 1600),
            "viewport_height": int(get("dpf_docs.viewport_height") or 900),
            "scale": int(get("dpf_docs.device_scale") or 2),
        }

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    @api.model
    def capture_module(self, doc_module, only_missing=True):
        """Capture screenshots for every documentable screen of one module.

        :param doc_module: a single ``doc.module`` record.
        :param only_missing: when ``True`` (default) only screens that do not
            yet have a captured screenshot are processed; manual uploads and
            already-captured screens are left untouched.
        :returns: dict with ``captured`` / ``failed`` / ``skipped`` counts.
        """
        doc_module.ensure_one()
        self._ensure_available()

        menus = doc_module.menu_ids.filtered(lambda m: m.web_url)
        if only_missing:
            menus = menus.filtered(
                lambda m: m.capture_state != "captured" or not m.screenshot
            )
        if not menus:
            return {"captured": 0, "failed": 0, "skipped": 0}

        return self._run_capture(menus)

    @api.model
    def capture_menus(self, menus):
        """Capture screenshots for an explicit recordset of ``doc.menu``."""
        self._ensure_available()
        menus = menus.filtered(lambda m: m.web_url)
        if not menus:
            return {"captured": 0, "failed": 0, "skipped": 0}
        return self._run_capture(menus)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ensure_available(self):
        """Raise a clear error when Playwright is not installed."""
        if not self.is_available():
            raise UserError(_(
                "Automatic screenshots require the 'playwright' Python package "
                "and its Chromium browser inside the Odoo container.\n\n"
                "Install them once with:\n"
                "  pip install playwright\n"
                "  playwright install --with-deps chromium\n\n"
                "Until then you can keep uploading screenshots manually on each "
                "function."
            ))

    def _run_capture(self, menus):
        """Capture screenshots for ``menus`` via an isolated child process.

        The browser work runs in ``screenshot_runner.py`` so Playwright gets a
        clean event loop independent of Odoo's worker threads.
        """
        cfg = self._config()
        captured, failed = 0, 0

        with tempfile.TemporaryDirectory(prefix="dpf_shots_") as workdir:
            spec = {
                "base_url": cfg["base_url"],
                "login": cfg["login"],
                "password": cfg["password"],
                "database": cfg["database"],
                "ready_selector": cfg["ready_selector"],
                "capture_selector": cfg["capture_selector"],
                "render_timeout": cfg["render_timeout"],
                "viewport_width": cfg["viewport_width"],
                "viewport_height": cfg["viewport_height"],
                "scale": cfg["scale"],
                "output_dir": workdir,
                "tasks": [
                    {"menu_id": m.id, "web_url": m.web_url, "name": m.name or ""}
                    for m in menus
                ],
            }
            spec_path = os.path.join(workdir, "spec.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)

            result = self._invoke_runner(spec_path, cfg["render_timeout"], menus)

            # Read back captured PNGs and store them on their menus.
            menu_by_id = {m.id: m for m in menus}
            for item in result.get("results", []):
                menu = menu_by_id.get(item.get("menu_id"))
                if not menu:
                    continue
                if item.get("error"):
                    failed += 1
                    menu.write({
                        "capture_state": "error",
                        "capture_error": str(item["error"])[:500],
                    })
                    continue
                png_path = item.get("path")
                if png_path and os.path.exists(png_path):
                    with open(png_path, "rb") as fh:
                        png_bytes = fh.read()
                    menu.write({
                        "screenshot": base64.b64encode(png_bytes),
                        "screenshot_filename": "menu_%s.png" % menu.id,
                        "capture_state": "captured",
                        "capture_error": False,
                    })
                    captured += 1
                else:
                    failed += 1
                    menu.write({
                        "capture_state": "error",
                        "capture_error": "Runner reported success but no file.",
                    })

        return {"captured": captured, "failed": failed, "skipped": 0}

    def _invoke_runner(self, spec_path, render_timeout, menus):
        """Run the child process and return its parsed JSON result.

        A generous wall-clock timeout (login + per-task render) guards against a
        hung browser. Any failure marks every menu as errored and surfaces a
        clear message.
        """
        # Allow login + one render per task, with a safety margin, in seconds.
        budget = max(120, int((render_timeout / 1000.0) * (len(menus) + 2)))
        try:
            proc = subprocess.run(
                [sys.executable, _RUNNER, spec_path],
                capture_output=True,
                text=True,
                timeout=budget,
            )
        except subprocess.TimeoutExpired:
            raise UserError(_(
                "Screenshot capture timed out after %s seconds. The Odoo web "
                "client may be slow to load, or the capture login/password is "
                "wrong. Check the 'dpf_docs.capture_login' / "
                "'dpf_docs.capture_password' system parameters."
            ) % budget)

        if proc.returncode != 0:
            # The runner prints a JSON error on stdout when it can; otherwise
            # fall back to stderr so the user sees the real cause.
            detail = (proc.stdout or proc.stderr or "").strip()[:800]
            _logger.error("Screenshot runner failed: %s", detail)
            raise UserError(_(
                "Automatic screenshot capture failed.\n\n%s"
            ) % (detail or "No diagnostic output from the capture process."))

        try:
            return json.loads(proc.stdout or "{}")
        except (ValueError, TypeError):
            _logger.error("Bad runner output: %s", proc.stdout)
            raise UserError(_(
                "The screenshot capture process returned unreadable output."
            ))
