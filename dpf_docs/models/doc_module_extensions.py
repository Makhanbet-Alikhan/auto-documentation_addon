# -*- coding: utf-8 -*-
"""
Extension models for doc.module.

These models store documentation data that the standard ORM introspector
cannot collect automatically:

  doc.workflow.state     — lifecycle states and transitions of the main object
  doc.inherited.model    — base Odoo models extended via _inherit
  doc.inherited.field    — individual fields added to inherited models
  doc.integration        — external services (MinIO, RabbitMQ, SMTP, etc.)
  doc.analytic.field     — computed KPI fields not shown in standard field list
  doc.export.action      — PDF/XLSX export buttons on forms

All models are generic and work for ANY Odoo addon, not just dpf_events.
They are populated manually (or via future automated introspectors) through
the doc.module form view.
"""
from odoo import fields, models


class DocWorkflowState(models.Model):
    """
    One row = one state in the lifecycle of the module's primary model.

    Examples for dpf_events:
      name='draft'     label='Черновик'   button_label='В черновик'
      name='confirmed' label='Подтверждено' button_label='Подтвердить'
      name='ongoing'   label='Идёт'        button_label='Начать'
      name='done'      label='Завершено'  button_label='Завершить'
      name='cancelled' label='Отменено'  button_label='Отменить'
    """
    _name = 'doc.workflow.state'
    _description = 'Auto Doc - Workflow State'
    _order = 'sequence asc, id asc'

    doc_module_id = fields.Many2one(
        'doc.module', string='Module', ondelete='cascade', required=True, index=True
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='State Key', required=True,
        help='Technical state value, e.g. draft, confirmed, done.'
    )
    label = fields.Char(
        string='Label',
        help='Human-readable state name shown in the UI status bar.'
    )
    description = fields.Text(
        string='Description',
        help='What this state means from the user perspective.'
    )
    transitions = fields.Text(
        string='Possible Transitions',
        help='Comma or newline separated list of states reachable from this one.'
    )
    button_label = fields.Char(
        string='Trigger Button',
        help='Label of the button that moves the object INTO this state.'
    )


class DocInheritedModel(models.Model):
    """
    A base Odoo model that the addon extends via _inherit.

    When an addon adds fields to event.event, res.partner, etc. without
    creating its own top-level menu, the introspector misses those fields.
    This model lets the documentation author record them manually.
    """
    _name = 'doc.inherited.model'
    _description = 'Auto Doc - Inherited Model Extension'
    _order = 'sequence asc, id asc'

    doc_module_id = fields.Many2one(
        'doc.module', string='Module', ondelete='cascade', required=True, index=True
    )
    sequence = fields.Integer(default=10)
    base_model = fields.Char(
        string='Base Model', required=True,
        help='Technical name of the extended model, e.g. event.event'
    )
    description = fields.Text(
        string='Extension Description',
        help='What functionality or fields this extension adds.'
    )
    field_ids = fields.One2many(
        'doc.inherited.field', 'inherited_model_id', string='Added Fields'
    )


class DocInheritedField(models.Model):
    """
    One field added by the addon to an inherited base model.
    """
    _name = 'doc.inherited.field'
    _description = 'Auto Doc - Field Added to Inherited Model'
    _order = 'sequence asc, id asc'

    inherited_model_id = fields.Many2one(
        'doc.inherited.model', string='Parent Model',
        ondelete='cascade', required=True, index=True
    )
    sequence = fields.Integer(default=10)
    field_name = fields.Char(
        string='Field Name', required=True,
        help='Technical field name, e.g. dpf_state'
    )
    field_type = fields.Char(
        string='Field Type',
        help='Odoo field type: Char, Many2one, Selection, Boolean, etc.'
    )
    description = fields.Text(
        string='Description',
        help='What this field stores and how it is used.'
    )
    is_required = fields.Boolean(string='Required', default=False)
    is_computed = fields.Boolean(
        string='Computed', default=False,
        help='True if this field is computed/readonly (store=False or compute=...).'
    )


class DocIntegration(models.Model):
    """
    An external service that the addon calls from its services/ layer.

    Examples: MinIO (file storage), RabbitMQ (message queue),
    Auth Service (employee validation), Reporting Service (PDF/XLSX generation).
    """
    _name = 'doc.integration'
    _description = 'Auto Doc - External Integration'
    _order = 'sequence asc, id asc'

    doc_module_id = fields.Many2one(
        'doc.module', string='Module', ondelete='cascade', required=True, index=True
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Service Name', required=True,
        help='Human-readable service name, e.g. MinIO, RabbitMQ, SMTP'
    )
    protocol = fields.Char(
        string='Protocol',
        help='Communication protocol: HTTP, AMQP, SMTP, gRPC, etc.'
    )
    purpose = fields.Text(
        string='Purpose',
        help='What this integration does from the user / business perspective.'
    )
    config_hint = fields.Text(
        string='Configuration Hint',
        help='How to enable or configure this integration (env vars, settings menu, etc.).'
    )


class DocAnalyticField(models.Model):
    """
    A computed / aggregated KPI field that is not shown in the standard
    fields_get() list because it has compute=True and store=False.

    Examples: dpf_total_registrations, dpf_attendance_rate, dpf_speaker_count.
    """
    _name = 'doc.analytic.field'
    _description = 'Auto Doc - Analytic / Computed Field'
    _order = 'sequence asc, id asc'

    doc_module_id = fields.Many2one(
        'doc.module', string='Module', ondelete='cascade', required=True, index=True
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Field / Indicator Name', required=True,
        help='Human-readable name of the KPI or computed field.'
    )
    description = fields.Text(
        string='Description',
        help='What this indicator shows to the user.'
    )
    formula_hint = fields.Text(
        string='Formula / Source',
        help='Brief description of how the value is calculated or where it comes from.'
    )


class DocExportAction(models.Model):
    """
    An export button (PDF, XLSX, CSV, etc.) available on a form or list view.

    These actions are defined as ir.actions.server or report actions and
    are invisible to fields_get(), so they must be documented here.
    """
    _name = 'doc.export.action'
    _description = 'Auto Doc - Export Action'
    _order = 'sequence asc, id asc'

    doc_module_id = fields.Many2one(
        'doc.module', string='Module', ondelete='cascade', required=True, index=True
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Action Name', required=True,
        help='Human-readable button name, e.g. "Скачать PDF"'
    )
    format = fields.Char(
        string='Format',
        help='Output format: PDF, XLSX, CSV, JSON, etc.'
    )
    description = fields.Text(
        string='Description',
        help='What data is exported and where the resulting file can be used.'
    )
