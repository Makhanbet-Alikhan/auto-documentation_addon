# -*- coding: utf-8 -*-
"""Standalone screenshot runner (child process).

Run as::

    python screenshot_runner.py /path/to/spec.json

The spec is a JSON object produced by ``doc.screenshot.capturer``::

    {
        "base_url": "http://localhost:8069",
        "login": "admin",
        "password": "admin",
        "database": "odoo19_db4",
        "ready_selector": ".o_content",
        "capture_selector": ".o_action_manager",
        "render_timeout": 30000,
        "viewport_width": 1600,
        "viewport_height": 900,
        "scale": 2,
        "output_dir": "/tmp/dpf_shots_xxx",
        "tasks": [{"menu_id": 12, "web_url": "/odoo/action-42", "name": "..."}]
    }

It logs into the Odoo web client once, screenshots each task's screen to a PNG
in ``output_dir`` and prints a JSON result on stdout::

    {"results": [{"menu_id": 12, "path": "/tmp/.../menu_12.png"},
                 {"menu_id": 13, "error": "..."}]}

This script runs in a *separate process* on purpose: Playwright's sync API needs
a clean event loop, which a fresh Python process guarantees -- unlike Odoo's
HTTP worker threads. It has no Odoo imports, so it stays fast and dependency-free
beyond Playwright itself.
"""
import json
import os
import sys


def _fail(message):
    """Print a JSON error envelope and exit non-zero."""
    print(json.dumps({"error": message}))
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        _fail("usage: screenshot_runner.py <spec.json>")

    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    # Import Playwright lazily so a missing install yields a clean message.
    try:
        from playwright.sync_api import TimeoutError as PWTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        _fail(
            "Playwright is not installed in the Odoo environment: %s. "
            "Run 'pip install playwright' and "
            "'playwright install --with-deps chromium'." % exc
        )

    base_url = spec["base_url"].rstrip("/")
    timeout = int(spec.get("render_timeout", 30000))
    ready_selector = spec.get("ready_selector", ".o_content")
    capture_selector = spec.get("capture_selector", ".o_action_manager")
    output_dir = spec["output_dir"]
    results = []

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # noqa: BLE001
            _fail(
                "Could not launch Chromium: %s. Run "
                "'playwright install --with-deps chromium' in the Odoo "
                "container." % exc
            )

        context = browser.new_context(
            viewport={
                "width": int(spec.get("viewport_width", 1600)),
                "height": int(spec.get("viewport_height", 900)),
            },
            device_scale_factor=int(spec.get("scale", 2)),
        )
        page = context.new_page()

        # --- Log in once ------------------------------------------------
        try:
            page.goto(
                "%s/web/login" % base_url,
                wait_until="domcontentloaded",
                timeout=timeout,
            )
            db_field = page.locator('select[name="db"], input[name="db"]')
            if db_field.count():
                try:
                    db_field.first.fill(spec.get("database", ""))
                except Exception:  # noqa: BLE001
                    pass
            page.fill('input[name="login"]', spec["login"])
            page.fill('input[name="password"]', spec["password"])
            page.click('button[type="submit"]')
            page.wait_for_selector(
                ".o_main_navbar, .o_web_client", timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001
            context.close()
            browser.close()
            _fail(
                "Login to %s failed: %s. Check 'dpf_docs.base_url', "
                "'dpf_docs.capture_login' and 'dpf_docs.capture_password'."
                % (base_url, exc)
            )

        # --- Capture each screen ---------------------------------------
        for task in spec.get("tasks", []):
            menu_id = task.get("menu_id")
            web_url = task.get("web_url")
            try:
                page.goto(
                    "%s%s" % (base_url, web_url),
                    wait_until="domcontentloaded",
                    timeout=timeout,
                )
                page.wait_for_selector(
                    ready_selector, state="visible", timeout=timeout
                )
                # Let the Owl view settle; tolerate screens that never idle.
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout)
                except PWTimeoutError:
                    pass
                # Small settle delay for late-rendering widgets.
                page.wait_for_timeout(600)

                target = page.locator(capture_selector).first
                png_path = os.path.join(output_dir, "menu_%s.png" % menu_id)
                if target.count():
                    target.screenshot(type="png", path=png_path)
                else:
                    page.screenshot(type="png", full_page=False, path=png_path)
                results.append({"menu_id": menu_id, "path": png_path})
            except Exception as exc:  # noqa: BLE001
                results.append({"menu_id": menu_id, "error": str(exc)[:500]})

        context.close()
        browser.close()

    print(json.dumps({"results": results}))


if __name__ == "__main__":
    main()
