# -*- coding: utf-8 -*-
"""Wizard: lets the user pick a project and writes it back to doc.generation.

We cannot use fields.Many2one('project.project') directly because that
causes an AssertionError at registry setup time when the 'project' module
is not installed.  Instead we store the project name as a plain Char and
resolve it to an ID in action_confirm().

After confirming, the wizard writes the project ID + name to doc.generation
and triggers a full page reload of the parent form via 'reload' client action.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DocProjectPickerWizard(models.TransientModel):
    _name = 'doc.project.picker.wizard'
    _description = 'Auto Doc — Project Picker'

    generation_id = fields.Many2one(
        'doc.generation',
        string='Generation Run',
        required=True,
        ondelete='cascade',
    )
    # Plain Char — user types or selects project name.
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
        """Resolve project name to ID, write to doc.generation, reload parent.

        The wizard opens as target='new' (a dialog).  To force the parent form
        to show the updated project_task_project_name / project_task_project_id
        we return ir.actions.client tag='reload' which triggers a full page
        reload — the cleanest approach for dialogs that mutate parent records.
        """
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
            raise UserError(_('Project «%s» not found.') % name)

        # Persist the selection on the generation record.
        gen.write({
            'project_task_project_id': project.id,
            'project_task_project_name': project.name,
        })

        # Reload the whole page so the parent form shows the new values.
        # This is the correct pattern when a dialog mutates its parent record.
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}
