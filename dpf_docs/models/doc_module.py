# -*- coding: utf-8 -*-
"""Stored documentation for one module (the aggregate result)."""
import base64
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Selection field names treated as workflow state carriers.
_WORKFLOW_STATE_FIELDS = ["state", "dpf_state", "dpf_status", "status"]

# ir.config_parameter key patterns that belong to Odoo core.
_ODOO_CORE_PARAM_PATTERNS = {"reporting"}

# Pattern to detect action methods: action_<verb>
_ACTION_METHOD_RE = re.compile(r'^action_\w+$')


class DocModule(models.Model):
    _name = "doc.module"
    _description = "Auto Doc - Documented Module"
    _order = "create_date desc"

    name = fields.Char(string="Title", required=True)
    generation_id = fields.Many2one(
        "doc.generation", string="Generation Run", ondelete="cascade"
    )
    technical_name = fields.Char(
        string="Module", required=True, help="Technical module name."
    )
    description = fields.Text(string="Module Description")

    primary_model = fields.Char(
        string="Primary Model",
        help="Technical name of the main model of this module (auto-detected).",
    )

    system_name = fields.Char(string="System Name")
    manual_version = fields.Char(string="Manual Version", default="1.0")
    developer = fields.Char(string="Developer")
    city_year = fields.Char(string="City / Year")
    platform_version = fields.Char(string="Platform Version", default="Odoo 19")
    intro_user_categories = fields.Text(string="1.1 User Categories")
    intro_scope = fields.Text(string="1.2 Scope")
    intro_purpose = fields.Text(string="1.3 Document Purpose")
    intro_conventions = fields.Text(string="1.4 Conventions")
    content_purpose = fields.Text(string="2.1 Purpose")
    content_materials = fields.Text(string="2.2 Materials")
    content_preparation = fields.Text(string="2.3 Preparation")
    bibliography = fields.Text(string="8 Bibliography")
    glossary = fields.Text(string="9 Glossary")

    menu_ids = fields.One2many("doc.menu", "doc_module_id", string="Menus")
    model_ids = fields.One2many("doc.model.info", "doc_module_id", string="Models")
    function_ids = fields.One2many("doc.function", "doc_module_id", string="Functions")

    workflow_state_ids = fields.One2many(
        "doc.workflow.state", "doc_module_id",
        string="3. Lifecycle States",
        help="States and transitions of the main object (workflow/state machine). "
             "When filled, Section 3 appears in the generated Word document.",
    )
    inherited_model_ids = fields.One2many(
        "doc.inherited.model", "doc_module_id",
        string="4. Inherited Model Extensions",
        help="Base Odoo models extended via _inherit. Fields added to those models "
             "are missed by the standard introspector. "
             "When filled, Section 4 appears in the generated Word document.",
    )
    integration_ids = fields.One2many(
        "doc.integration", "doc_module_id",
        string="5. External Integrations",
        help="External services used by the module (MinIO, RabbitMQ, SMTP, etc.). "
             "When filled, Section 5 appears in the generated Word document.",
    )
    analytic_field_ids = fields.One2many(
        "doc.analytic.field", "doc_module_id",
        string="7. Analytic Fields",
        help="Computed KPI fields not visible in the standard field list. "
             "When filled, Section 7 appears in the generated Word document.",
    )
    export_action_ids = fields.One2many(
        "doc.export.action", "doc_module_id",
        string="7. Export Actions",
        help="PDF/XLSX/CSV export buttons on forms or list views. "
             "When filled, Section 7 appears in the generated Word document.",
    )

    menu_count = fields.Integer(string="Menus", compute="_compute_counts", store=True)
    model_count = fields.Integer(string="Models", compute="_compute_counts", store=True)
    captured_count = fields.Integer(string="Screenshots", compute="_compute_counts", store=True)
    function_count = fields.Integer(string="Functions", compute="_compute_counts", store=True)

    markdown = fields.Text(string="Markdown Output")
    pdf_attachment_id = fields.Many2one("ir.attachment", string="PDF File")
    word_attachment_id = fields.Many2one("ir.attachment", string="Word File")

    @api.depends("menu_ids", "model_ids", "function_ids", "menu_ids.capture_state")
    def _compute_counts(self):
        for rec in self:
            rec.menu_count = len(rec.menu_ids)
            rec.model_count = len(rec.model_ids)
            rec.function_count = len(rec.function_ids)
            rec.captured_count = len(
                rec.menu_ids.filtered(lambda m: m.capture_state == "captured")
            )

    # ------------------------------------------------------------------
    # Auto-population of extension sections (3, 4, 5, 7)
    # ------------------------------------------------------------------

    @staticmethod
    def _table_exists(cr, table_name):
        cr.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s LIMIT 1
            """,
            (table_name,),
        )
        return bool(cr.fetchone())

    def _get_own_models(self):
        """Return set of model technical names that belong to this addon."""
        module_name = self.technical_name
        if not module_name:
            return set()

        own = set()
        cr = self.env.cr

        for rel_table in ("ir_model_module", "ir_module_module_model_ids_rel"):
            if not self._table_exists(cr, rel_table):
                continue
            cr.execute(
                """
                SELECT im.model
                FROM ir_model im
                JOIN {table} rel ON rel.model_id = im.id
                JOIN ir_module_module mod ON mod.id = rel.module_id
                WHERE mod.name = %s
                """.format(table=rel_table),
                (module_name,),
            )
            rows = cr.fetchall()
            if rows:
                own = {r[0] for r in rows}
                break

        prefixes = set()
        naive = module_name.replace("_", ".") + ".%"
        prefixes.add(naive)
        if module_name.endswith("s"):
            singular = module_name[:-1].replace("_", ".") + ".%"
            prefixes.add(singular)
        parts = module_name.split("_")
        if len(parts) >= 2:
            prefixes.add(".".join(parts) + ".%")

        for prefix in prefixes:
            cr.execute("SELECT model FROM ir_model WHERE model LIKE %s", (prefix,))
            own |= {r[0] for r in cr.fetchall()}

        exact_candidates = set()
        exact_candidates.add(module_name.replace("_", "."))
        if module_name.endswith("s"):
            exact_candidates.add(module_name[:-1].replace("_", "."))
        parts = module_name.split("_")
        if len(parts) >= 2:
            exact_candidates.add(".".join(parts))

        if exact_candidates:
            cr.execute(
                "SELECT model FROM ir_model WHERE model IN %s",
                (tuple(exact_candidates),),
            )
            own |= {r[0] for r in cr.fetchall()}

        return {m for m in own if m}

    def _get_module_models(self):
        """Return the set of *own* model names for this addon."""
        self.ensure_one()
        own = self._get_own_models()
        if self.primary_model:
            own.add(self.primary_model)
        prefix = self.technical_name.replace("_", ".") + "."
        for mi in self.model_ids:
            if mi.technical_name and mi.technical_name.startswith(prefix):
                own.add(mi.technical_name)
        return {m for m in own if m}

    def _detect_action_buttons(self, model_name, state_field_name):
        btn_map = {}
        try:
            import inspect
            model_cls = type(self.env[model_name])
            for attr_name in dir(model_cls):
                if not _ACTION_METHOD_RE.match(attr_name):
                    continue
                method = getattr(model_cls, attr_name, None)
                if not callable(method):
                    continue
                try:
                    src = inspect.getsource(method)
                    pattern = re.compile(
                        r'["\']' + re.escape(state_field_name) + r'["\']\s*:\s*["\']([\w]+)["\']'
                    )
                    for match in pattern.finditer(src):
                        target_state = match.group(1)
                        human = (
                            attr_name
                            .replace("action_", "")
                            .replace("dpf_", "")
                            .replace("_", " ")
                            .title()
                        )
                        btn_map[target_state] = human
                except Exception:
                    pass
        except Exception:
            pass
        return btn_map

    def _detect_transitions(self, model_name, state_field_name, selection):
        state_values = [v for v, _ in selection]
        transition_map = {v: [] for v in state_values}

        for i, value in enumerate(state_values[:-1]):
            transition_map[value].append(state_values[i + 1])

        try:
            import inspect
            model_cls = type(self.env[model_name])
            for attr_name in dir(model_cls):
                if not _ACTION_METHOD_RE.match(attr_name):
                    continue
                method = getattr(model_cls, attr_name, None)
                if not callable(method):
                    continue
                try:
                    src = inspect.getsource(method)
                    pattern = re.compile(
                        r'["\']' + re.escape(state_field_name) + r'["\']\s*:\s*["\']([\w]+)["\']'
                    )
                    written_states = [m.group(1) for m in pattern.finditer(src)]
                    if not written_states:
                        continue
                    target = written_states[0]
                    for from_state in state_values:
                        if from_state != target and target not in transition_map[from_state]:
                            if any(kw in attr_name for kw in ("cancel", "reset", "abort")):
                                transition_map[from_state].append(target)
                except Exception:
                    pass
        except Exception:
            pass

        return transition_map

    def _populate_workflow_states(self):
        self.ensure_one()
        self.workflow_state_ids.unlink()
        model_names = self._get_module_models()
        IrModelFields = self.env["ir.model.fields"]

        for model_name in model_names:
            state_field = None
            for candidate in _WORKFLOW_STATE_FIELDS:
                field = IrModelFields.search([
                    ("model", "=", model_name),
                    ("name", "=", candidate),
                    ("ttype", "=", "selection"),
                ], limit=1)
                if field:
                    state_field = field
                    break

            if not state_field:
                continue

            try:
                selection = self.env[model_name].fields_get(
                    [state_field.name]
                )[state_field.name]["selection"]
            except Exception:
                _logger.warning(
                    "Failed to introspect state selection for model %s (field %s)",
                    model_name, state_field.name, exc_info=True,
                )
                continue

            btn_map = self._detect_action_buttons(model_name, state_field.name)
            transition_map = self._detect_transitions(model_name, state_field.name, selection)

            seq = 10
            for value, label in selection:
                self.env["doc.workflow.state"].create({
                    "doc_module_id": self.id,
                    "sequence": seq,
                    "name": value,
                    "label": label,
                    "transitions": ", ".join(transition_map.get(value, [])) or False,
                    "button_label": btn_map.get(value, "") or False,
                })
                seq += 10

    def _get_module_field_models(self):
        """Return {base_model: [field_records]} for fields added by this addon
        to models it does NOT own (i.e. _inherit extensions).
        """
        self.ensure_one()
        module_name = self.technical_name
        if not module_name:
            return {}

        own_models = self._get_own_models()
        IrModelFields = self.env["ir.model.fields"]
        cr = self.env.cr
        by_model = {}

        cr.execute(
            """
            SELECT res_id
            FROM ir_model_data
            WHERE module = %s
              AND model = 'ir.model.fields'
              AND res_id IS NOT NULL
            """,
            (module_name,),
        )
        field_ids = [r[0] for r in cr.fetchall()]
        if field_ids:
            fields_qs = IrModelFields.browse(field_ids).exists()
            for f in fields_qs:
                if f.model in own_models:
                    continue
                by_model.setdefault(f.model, []).append(f)

        if not by_model:
            prefix = module_name + "_%"
            own_dot_prefix = module_name.replace("_", ".") + "."
            candidate_fields = IrModelFields.search([
                ("name", "=like", prefix),
                ("model", "not in", list(own_models)),
            ])
            for f in candidate_fields:
                if "." in f.model and not f.model.startswith(own_dot_prefix):
                    by_model.setdefault(f.model, []).append(f)

        return by_model

    # System/chatter field names to skip in section 4
    _SECTION4_SKIP_NAMES = frozenset({
        'id', 'display_name', '__last_update',
        'create_uid', 'create_date', 'write_uid', 'write_date',
        # mail.thread
        'message_is_follower', 'message_follower_ids', 'message_partner_ids',
        'message_ids', 'has_message', 'message_needaction',
        'message_needaction_counter', 'message_has_error',
        'message_has_error_counter', 'message_attachment_count',
        'message_main_attachment_id', 'message_unread_counter',
        'message_bounce', 'message_has_sms_error', 'website_message_ids',
        # mail.activity.mixin
        'activity_ids', 'activity_state', 'activity_user_id',
        'activity_type_id', 'activity_type_icon', 'activity_date_deadline',
        'my_activity_date_deadline', 'activity_summary',
        'activity_exception_decoration', 'activity_exception_icon',
        'activity_count',
        # portal / access
        'access_url', 'access_token', 'access_warning',
        # rating
        'rating_ids', 'rating_last_value', 'rating_avg',
        # website
        'website_published', 'is_published', 'can_publish',
        'website_url', 'website_id',
        'website_meta_title', 'website_meta_description',
        'website_meta_keywords', 'website_meta_og_img',
        'seo_name', 'website_slug', 'website_indexed',
        'is_seo_optimized', 'footer_visible', 'header_visible',
    })
    _SECTION4_SKIP_PREFIXES = (
        'message_', 'activity_', 'website_message_', '__',
        'website_meta_', 'seo_',
    )

    def _is_section4_skip(self, field_rec):
        """Return True for system/chatter/website fields to exclude from section 4."""
        name = field_rec.name or ''
        if name in self._SECTION4_SKIP_NAMES:
            return True
        if any(name.startswith(p) for p in self._SECTION4_SKIP_PREFIXES):
            return True
        return False

    def _populate_inherited_models(self):
        """Detect models extended via _inherit for this addon."""
        self.ensure_one()
        self.inherited_model_ids.unlink()

        by_model = self._get_module_field_models()

        for base_model, field_list in by_model.items():
            # Filter out system / chatter / website fields
            business_fields = [f for f in field_list if not self._is_section4_skip(f)]
            if not business_fields:
                continue  # skip model entirely if nothing left

            inherited = self.env["doc.inherited.model"].create({
                "doc_module_id": self.id,
                "base_model": base_model,
            })
            seq = 10
            for f in business_fields:
                self.env["doc.inherited.field"].create({
                    "inherited_model_id": inherited.id,
                    "sequence": seq,
                    "field_name": f.name,
                    "field_type": f.ttype,
                    "description": f.field_description or False,
                    "is_required": f.required,
                    "is_computed": bool(f.compute),
                })
                seq += 10

    def _populate_export_actions(self):
        self.ensure_one()
        self.export_action_ids.unlink()

        module_name = self.technical_name
        if not module_name:
            return

        cr = self.env.cr
        seq = 10

        cr.execute(
            """
            SELECT res_id FROM ir_model_data
            WHERE module = %s AND model = 'ir.actions.report'
              AND res_id IS NOT NULL
            """,
            (module_name,),
        )
        report_ids = [r[0] for r in cr.fetchall()]
        if report_ids:
            reports = self.env["ir.actions.report"].browse(report_ids).exists()
            for report in reports:
                self.env["doc.export.action"].create({
                    "doc_module_id": self.id,
                    "sequence": seq,
                    "name": report.name,
                    "format": (report.report_type or "").upper(),
                    "description": report.report_name or "",
                })
                seq += 10

        cr.execute(
            """
            SELECT res_id FROM ir_model_data
            WHERE module = %s AND model = 'ir.actions.server'
              AND res_id IS NOT NULL
            """,
            (module_name,),
        )
        server_ids = [r[0] for r in cr.fetchall()]
        if server_ids:
            servers = self.env["ir.actions.server"].browse(server_ids).exists()
            for action in servers:
                self.env["doc.export.action"].create({
                    "doc_module_id": self.id,
                    "sequence": seq,
                    "name": action.name,
                    "format": "SERVER",
                    "description": action.state or "",
                })
                seq += 10

    def _populate_analytic_fields(self):
        self.ensure_one()
        self.analytic_field_ids.unlink()

        own_models = self._get_module_models()
        all_model_names = own_models | {
            mi.technical_name for mi in self.model_ids if mi.technical_name
        }
        cr = self.env.cr
        cr.execute(
            """
            SELECT DISTINCT im.model
            FROM ir_model_data imd
            JOIN ir_model im ON im.id = imd.res_id
            WHERE imd.module = %s AND imd.model = 'ir.model'
            """,
            (self.technical_name,),
        )
        all_model_names |= {r[0] for r in cr.fetchall()}

        if not all_model_names:
            return

        IrModelFields = self.env["ir.model.fields"]
        fields_qs = IrModelFields.search([
            ("model", "in", list(all_model_names)),
            ("store", "=", False),
            ("compute", "!=", False),
        ])

        seq = 10
        for f in fields_qs:
            self.env["doc.analytic.field"].create({
                "doc_module_id": self.id,
                "sequence": seq,
                "name": f.field_description or f.name,
                "description": "Computed field on %s" % f.model,
                "formula_hint": "compute=%s" % (f.compute or ""),
            })
            seq += 10

    def _populate_integrations(self):
        self.ensure_one()
        self.integration_ids.unlink()

        ICP = self.env["ir.config_parameter"].sudo()
        hints = [
            ("minio", "MinIO", "S3/HTTP"),
            ("amqp", "RabbitMQ", "AMQP"),
            ("rabbitmq", "RabbitMQ", "AMQP"),
            ("email_notification_service", "Email Notification Service", "HTTP/AMQP"),
            ("reporting_service", "Reporting Service", "HTTP"),
            ("auth_service", "Auth Service", "HTTP"),
        ]

        seen = set()
        seq = 10
        for pattern, service_name, protocol in hints:
            if pattern in _ODOO_CORE_PARAM_PATTERNS:
                continue
            params = ICP.search([("key", "ilike", pattern)])
            if not params or service_name in seen:
                continue
            seen.add(service_name)
            self.env["doc.integration"].create({
                "doc_module_id": self.id,
                "sequence": seq,
                "name": service_name,
                "protocol": protocol,
                "purpose": "Auto-detected via configuration parameter containing '%s'." % pattern,
            })
            seq += 10

    def auto_populate_extensions(self):
        """Populate sections 3-5 and 7 automatically from ORM introspection."""
        self.ensure_one()
        self._populate_workflow_states()
        self._populate_inherited_models()
        self._populate_export_actions()
        self._populate_analytic_fields()
        self._populate_integrations()

    # ------------------------------------------------------------------
    # Manual content helpers
    # ------------------------------------------------------------------
    def apply_manual_defaults(self):
        self.ensure_one()
        composer = self.env["doc.text.defaults"]
        defaults = composer.manual_defaults(self)
        values = {field: value for field, value in defaults.items() if not self[field]}
        if values:
            self.write(values)
        return True

    @staticmethod
    def _menu_has_form(menu):
        view_modes = [
            v.strip() for v in (menu.view_modes or "").split(",") if v.strip()
        ]
        if not view_modes:
            return bool(menu.res_model)
        return "form" in view_modes

    def build_functions_from_menus(self):
        self.ensure_one()
        self.function_ids.unlink()
        composer = self.env["doc.text.defaults"]
        number = 0
        menus = self.menu_ids.sorted(
            key=lambda m: ((m.sequence or 999999), (m.complete_name or ""), m.id)
        )
        seen = set()
        for menu in menus:
            if menu.capture_state == "skipped" and not menu.res_model:
                continue
            normalized_views = ",".join(sorted(set(
                v.strip() for v in (menu.view_modes or "").split(",") if v.strip()
            )))
            dedupe_key = (
                (menu.name or "").strip().lower(),
                (menu.res_model or "").strip().lower(),
                normalized_views,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            number += 1
            entry = composer.function_for_menu(menu, number)
            entry.update({
                "doc_module_id": self.id,
                "doc_menu_id": menu.id,
                "sequence": number * 10,
                "number": number,
                "screenshot": menu.screenshot or False,
                "screenshot_source": "menu" if menu.screenshot else "none",
            })
            self.env["doc.function"].create(entry)

            if self._menu_has_form(menu):
                number += 1
                create_entry = composer.function_for_create(menu, number)
                create_entry.update({
                    "doc_module_id": self.id,
                    "doc_menu_id": menu.id,
                    "sequence": number * 10,
                    "number": number,
                    "screenshot": menu.screenshot or False,
                    "screenshot_source": "menu" if menu.screenshot else "none",
                })
                self.env["doc.function"].create(create_entry)
        return True

    def capture_screenshots(self, only_missing=True):
        self.ensure_one()
        result = self.env["doc.screenshot.capturer"].capture_module(
            self, only_missing=only_missing
        )
        self.refresh_function_screenshots()
        return result

    def action_capture_screenshots(self):
        self.ensure_one()
        result = self.capture_screenshots(only_missing=True)
        message = _("Screenshots captured: %(captured)s, failed: %(failed)s.") % result
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Automatic Screenshots"),
                "message": message,
                "type": "success" if not result.get("failed") else "warning",
                "sticky": False,
            },
        }

    def refresh_function_screenshots(self):
        self.ensure_one()
        for func in self.function_ids.filtered(lambda f: f.doc_menu_id):
            if func.screenshot_source == "manual":
                continue
            menu = func.doc_menu_id
            if menu.capture_state == "captured" and menu.screenshot:
                func.screenshot = menu.screenshot
                func.screenshot_source = "menu"
        return True

    def pending_screenshot_tasks(self):
        self.ensure_one()
        return [
            menu.to_task_dict()
            for menu in self.menu_ids.filtered(
                lambda m: m.web_url and m.capture_state in ("pending", "error")
            )
        ]

    def action_render_markdown(self):
        self.ensure_one()
        try:
            md = self.env["doc.generation"]._render_markdown(self)
        except Exception as exc:
            _logger.error(
                "action_render_markdown: error for module %s: %s",
                self.technical_name, exc, exc_info=True,
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Error"),
                    "message": str(exc),
                    "type": "danger",
                    "sticky": True,
                },
            }
        self.with_context(no_recompute=True).write({"markdown": md})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Markdown"),
                "message": _("\u041c\u0430\u0440\u043a\u0434\u0430\u0443\u043d \u0441\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u043d."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_enrich_from_snapshot(self):
        self.ensure_one()
        generation = self.generation_id
        if not generation or not generation.snapshot_set_id:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Project Snapshot"),
                    "message": _(
                        "\u0412 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d Snapshot Set. "
                        "\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0437\u0430\u043f\u0438\u0441\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438 \u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 Project Snapshot Set."
                    ),
                    "type": "warning",
                    "sticky": True,
                },
            }
        stats = self.env["doc.project.enricher"].enrich_module(self, overwrite=False)
        if stats["reason"] == "no_matching_tasks":
            msg = _("\u0417\u0430\u0434\u0430\u0447 \u0441 \u0442\u0435\u0433\u043e\u043c [%s] \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e.") % self.technical_name
            notif_type = "warning"
        elif stats["reason"] == "enriched":
            msg = _(
                "\u041e\u0431\u043e\u0433\u0430\u0449\u0435\u043d\u043e: \u0444\u0443\u043d\u043a\u0446\u0438\u0439 %s, \u043c\u0435\u043d\u044e %s."
            ) % (stats["functions_enriched"], stats["menus_enriched"])
            notif_type = "success"
        else:
            msg = _("reason: %s") % stats["reason"]
            notif_type = "info"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Project Snapshot Enrichment"),
                "message": msg,
                "type": notif_type,
                "sticky": False,
            },
        }

    def action_print_pdf_manual(self):
        self.ensure_one()
        self._auto_capture_if_enabled()
        self.refresh_function_screenshots()
        return self.env.ref("dpf_docs.action_report_doc_module").report_action(self)

    def _auto_capture_if_enabled(self):
        self.ensure_one()
        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "dpf_docs.auto_capture", "1"
        )
        capturer = self.env["doc.screenshot.capturer"]
        if enabled not in ("1", "true", "True") or not capturer.is_available():
            return
        try:
            capturer.capture_module(self, only_missing=True)
        except Exception:
            _logger.warning(
                "Auto-capture failed for module %s.",
                self.technical_name, exc_info=True,
            )

    def _build_word_attachment(self):
        self.ensure_one()
        try:
            self.auto_populate_extensions()
        except Exception:
            _logger.warning(
                "auto_populate_extensions failed for module %s",
                self.technical_name, exc_info=True,
            )
        self._auto_capture_if_enabled()
        self.refresh_function_screenshots()
        data = self.env["doc.word.export"].build_docx(self)
        filename = "%s_module_documentation.docx" % (self.technical_name or "module")
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(data),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document",
        })
        self.word_attachment_id = attachment.id
        return attachment

    def action_download_word(self):
        self.ensure_one()
        attachment = self._build_word_attachment()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }
