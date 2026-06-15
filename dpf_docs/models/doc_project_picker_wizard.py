# -*- coding: utf-8 -*-
"""Wizard: lets the user pick a project and writes it back to doc.generation.

We cannot use fields.Many2one('project.project') directly because that
causes an AssertionError at registry setup time when the 'project' module
is not installed.  Instead we store the project name as a plain Char and
resolve it to an ID in action_confirm().
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DocProjectPickerWizard(models.TransientModel):
    _name = 'doc.project.picker.wizard'
    _description = 'Auto Doc \u2014 Project Picker'

    generation_id = fields.Many2one(
        'doc.generation',
        string='\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u044f',
        required=True,
        ondelete='cascade',
    )
    # Plain Char — user types or selects project name.
    # Resolved to integer ID in action_confirm() via env lookup.
    project_name = fields.Char(
        string='\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0435\u043a\u0442\u0430',
        required=True,
    )
    # Available project names for the selection widget (computed, not stored)
    project_name_selection = fields.Selection(
        selection='_get_project_selection',
        string='\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u043f\u0440\u043e\u0435\u043a\u0442',
    )

    @api.model
    def _get_project_selection(self):
        """Return list of (name, name) tuples for all projects."""
        if 'project.project' not in self.env:
            return []
        projects = self.env['project.project'].sudo().search(
            [], order='name asc', limit=200
        )
        return [(p.name, p.name) for p in projects]

    @api.onchange('project_name_selection')
    def _onchange_project_name_selection(self):
        if self.project_name_selection:
            self.project_name = self.project_name_selection

    def action_confirm(self):
        self.ensure_one()
        gen = self.generation_id
        if not gen:
            raise UserError(_('\u0421\u0432\u044f\u0437\u044c \u0441 \u0437\u0430\u043f\u0438\u0441\u044c\u044e \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u0438 \u043f\u043e\u0442\u0435\u0440\u044f\u043d\u0430.'))
        name = (self.project_name or '').strip()
        if not name:
            raise UserError(_('\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0435\u043a\u0442\u0430.'))
        if 'project.project' not in self.env:
            raise UserError(_('\u041c\u043e\u0434\u0443\u043b\u044c project \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d.'))
        project = self.env['project.project'].sudo().search(
            [('name', 'ilike', name)], limit=1
        )
        if not project:
            raise UserError(_('\u041f\u0440\u043e\u0435\u043a\u0442 \u00ab%s\u00bb \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.') % name)
        gen.write({
            'project_task_project_id': project.id,
            'project_task_project_name': project.name,
        })
        return {'type': 'ir.actions.act_window_close'}

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}
