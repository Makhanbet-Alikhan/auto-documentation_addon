# -*- coding: utf-8 -*-
"""Wizard: lets the user pick a project.project and writes it back
to the doc.generation record that opened the wizard.

Flow:
  1. User clicks 📂 on doc.generation form.
  2. action_pick_project() opens this wizard in target='new'.
  3. User selects a project from the Many2one dropdown.
  4. Clicks OK → action_confirm() writes project_task_project_id
     and project_task_project_name back to the parent doc.generation.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class DocProjectPickerWizard(models.TransientModel):
    _name = 'doc.project.picker.wizard'
    _description = 'Auto Doc — Project Picker'

    generation_id = fields.Many2one(
        'doc.generation',
        string='Документация',
        required=True,
        ondelete='cascade',
    )
    # We declare this as a Char + Integer pair so there’s no hard FK to project.
    project_id_int = fields.Integer(string='Project ID (internal)', default=0)
    project_name = fields.Char(string='Проект')

    # Dynamic Many2one rendered only when project module is present.
    # If project is not installed this field won’t resolve, but the
    # wizard won’t be opened at all (action_pick_project raises UserError).
    project_m2o = fields.Many2one(
        'project.project',
        string='Выбрать проект',
        ondelete='set null',
    )

    def action_confirm(self):
        self.ensure_one()
        gen = self.generation_id
        if not gen:
            raise UserError(_('Связь с записью документации потеряна.'))
        project = self.project_m2o
        if project:
            gen.write({
                'project_task_project_id': project.id,
                'project_task_project_name': project.name,
            })
        return {'type': 'ir.actions.act_window_close'}

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}
