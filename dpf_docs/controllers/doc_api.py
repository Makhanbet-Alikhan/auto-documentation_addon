# -*- coding: utf-8 -*-
"""HTTP endpoints consumed by the external Playwright worker.

Two routes:

* ``GET  /doc_gen/spec/<generation_id>`` -> the JSON spec (screenshot tasks).
* ``POST /doc_gen/upload``               -> store one captured screenshot.

The worker authenticates with a shared secret sent in the ``X-Doc-Token``
header (compared against the ``dpf_docs.worker_token`` config param),
so it does not need a full interactive user session for the upload call.
"""
import base64
import binascii
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class DocApiController(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _check_token(self):
        """Return True when the request carries the correct worker token."""
        expected = request.env["ir.config_parameter"].sudo().get_param(
            "dpf_docs.worker_token"
        )
        provided = request.httprequest.headers.get("X-Doc-Token")
        return bool(expected) and provided == expected

    def _json_response(self, payload, status=200):
        return request.make_response(
            json.dumps(payload),
            headers=[("Content-Type", "application/json")],
            status=status,
        )

    # ------------------------------------------------------------------
    # Spec endpoint
    # ------------------------------------------------------------------
    @http.route(
        "/doc_gen/spec/<int:generation_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    def get_spec(self, generation_id, **kwargs):
        """Return the screenshot spec for a generation run.

        Uses ``auth='user'`` so the caller's session also primes the browser
        login the worker reuses via storageState.
        """
        generation = request.env["doc.generation"].browse(generation_id).exists()
        if not generation:
            return self._json_response({"error": "not_found"}, status=404)
        return self._json_response(generation.get_worker_spec())

    # ------------------------------------------------------------------
    # Upload endpoint
    # ------------------------------------------------------------------
    @http.route(
        "/doc_gen/upload",
        type="http", auth="public", methods=["POST"], csrf=False,
    )
    def upload_screenshot(self, **kwargs):
        """Persist one screenshot (base64) onto its doc.menu record.

        Expected JSON body::

            {
                "doc_menu_id": 12,
                "filename": "menu_12.png",
                "image_b64": "...",
                "caption": "optional override caption",
                "error": "optional error message"
            }
        """
        if not self._check_token():
            return self._json_response({"error": "unauthorized"}, status=401)

        try:
            body = json.loads(request.httprequest.get_data() or b"{}")
        except (ValueError, TypeError):
            return self._json_response({"error": "bad_json"}, status=400)

        menu_id = body.get("doc_menu_id")
        menu = request.env["doc.menu"].sudo().browse(int(menu_id)).exists() \
            if menu_id else None
        if not menu:
            return self._json_response({"error": "menu_not_found"}, status=404)

        # Worker reported a capture failure.
        if body.get("error"):
            menu.write({
                "capture_state": "error",
                "capture_error": str(body["error"])[:500],
            })
            return self._json_response({"ok": True, "stored": False})

        image_b64 = body.get("image_b64")
        if not image_b64:
            return self._json_response({"error": "no_image"}, status=400)

        try:
            # Validate the payload decodes to real bytes before storing.
            base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError):
            return self._json_response({"error": "bad_base64"}, status=400)

        values = {
            "screenshot": image_b64,  # Binary fields accept base64 strings.
            "screenshot_filename": body.get("filename") or ("menu_%s.png" % menu.id),
            "capture_state": "captured",
            "capture_error": False,
        }
        if body.get("caption"):
            values["caption"] = body["caption"]
        menu.write(values)

        _logger.info("Stored screenshot for doc.menu %s", menu.id)
        return self._json_response({"ok": True, "stored": True})
