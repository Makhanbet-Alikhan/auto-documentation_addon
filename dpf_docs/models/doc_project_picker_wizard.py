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
        string='Generation Run',
        required=True,
        ondelete='cascade',
    )
    # Plain Char \u2014 user types or selects project name.
    # Resolved to integer ID in action_confirm() via env lookup.
    project_name = fields.Char(
        string='Project Name',
        required=True,
    )
    # Available project names for the selection widget (computed, not stored)
    project_name_selection = fields.Selection(
        selection='_get_project_selection',
        string='Select Project',
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
        """Copy the dropdown selection into the text field."""
        if self.project_name_selection:
            self.project_name = self.project_name_selection

    def action_confirm(self):
        """Resolve project name to ID, write to doc.generation, reload parent form."""
        self.ensure_one()
        gen = self.generation_id
        if not gen:
            raise UserError(_('Generation record link lost.'))
        name = (self.project_name or '').strip()
        if not name:
            raise UserError(_('Enter a project name.'))
        if 'project.project' not in self.env:
            raise UserError(_('The project module is not installed.'))
        project = self.env['project.project'].sudo().search(
            [('name', 'ilike', name)], limit=1
        )
        if not project:
            raise UserError(_('Project \u00ab%s\u00bb not found.') % name)

        # Write project ID + name back to the generation record.
        gen.write({
            'project_task_project_id': project.id,
            'project_task_project_name': project.name,
        })

        # Return reload action so the parent form reflects the new values
        # immediately without requiring a manual page refresh.
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'doc.generation',
            'res_id': gen.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}
