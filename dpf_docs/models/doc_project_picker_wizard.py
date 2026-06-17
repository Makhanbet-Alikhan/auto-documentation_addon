# -*- coding: utf-8 -*-
"""
Wizard: lets the user pick a project and writes it back to doc.generation.

Design notes
------------
- We cannot use fields.Many2one('project.project') directly because that
  causes an AssertionError at registry setup time when the 'project' module
  is not installed.  Instead we store the resolved integer project ID in a
  plain Integer field and populate the dropdown via a Selection whose values
  are stringified project IDs.

- After confirming, the wizard writes project_task_project_id +
  project_task_project_name to doc.generation and returns a 'reload' client
  action so the parent form view refreshes in place without navigating away.
  This is the correct pattern for Odoo 19 dialogs that need to refresh the
  parent form: returning act_window reopens the whole form and loses scroll
  position; 'reload' just refreshes the current view.

- required=True is intentionally NOT set on project_selection at the field
  level to avoid a DB NOT NULL constraint error during module upgrade when
  the transient table already contains NULL rows from the previous schema.
  Validation is enforced in action_confirm() via UserError instead.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DocProjectPickerWizard(models.TransientModel):
    _name = 'doc.project.picker.wizard'
    _description = 'Auto Doc - Project Picker Wizard'

    generation_id = fields.Many2one(
        'doc.generation',
        string='Generation Run',
        required=True,
        ondelete='cascade',
    )

    # Selection value = str(project.id) so we never do ilike name resolution.
    # required=True is NOT set here on purpose — see module docstring.
    project_selection = fields.Selection(
        selection='_get_project_selection',
        string='Project',
    )

    project_display_name = fields.Char(
        string='Selected project',
        readonly=True,
        store=False,
        compute='_compute_project_display_name',
    )

    # ------------------------------------------------------------------ #
    # Selection source                                                     #
    # ------------------------------------------------------------------ #

    @api.model
    def _get_project_selection(self):
        """
        Return [(str(project.id), project.name), ...] for all projects.

        Using the project ID as the stored value avoids name-resolution
        problems entirely (duplicate names, ilike ambiguity, etc.).
        """
        if 'project.project' not in self.env:
            return []
        projects = self.env['project.project'].sudo().search(
            [], order='name asc', limit=500
        )
        return [(str(p.id), p.name) for p in projects]

    @api.depends('project_selection')
    def _compute_project_display_name(self):
        for rec in self:
            if rec.project_selection and 'project.project' in self.env:
                try:
                    pid = int(rec.project_selection)
                    proj = self.env['project.project'].sudo().browse(pid)
                    rec.project_display_name = proj.name if proj.exists() else rec.project_selection
                except (ValueError, TypeError):
                    rec.project_display_name = rec.project_selection
            else:
                rec.project_display_name = ''

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def action_confirm(self):
        """
        Write the selected project to doc.generation and reload the parent form.

        Validation of project_selection is done here (not via field required=True)
        to avoid the DB NOT NULL constraint upgrade error.

        Returns a 'reload' client action so the parent doc.generation form
        refreshes in place and shows the newly saved project name.
        """
        self.ensure_one()
        gen = self.generation_id
        if not gen:
            raise UserError(_('Generation record link lost.'))

        if not self.project_selection:
            raise UserError(_('Please select a project from the list.'))

        if 'project.project' not in self.env:
            raise UserError(_('The Project module is not installed.'))

        try:
            project_id = int(self.project_selection)
        except (ValueError, TypeError):
            raise UserError(_('Invalid project selection value: %s') % self.project_selection)

        project = self.env['project.project'].sudo().browse(project_id)
        if not project.exists():
            raise UserError(_('The selected project no longer exists (id=%s).') % project_id)

        gen.write({
            'project_task_project_id': project.id,
            'project_task_project_name': project.name,
        })

        # 'reload' refreshes the current view (parent generation form) without
        # navigating away.  This is the correct way to close a dialog and
        # update the parent in Odoo 17+.
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_cancel(self):
        """Close the dialog without saving."""
        return {'type': 'ir.actions.act_window_close'}
