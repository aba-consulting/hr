import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EmployerSignatureWizard(models.TransientModel):
    _name = 'hr.payroll.employer.signature.wizard'
    _description = 'Configurar Firma del Empleador'

    company_id = fields.Many2one(
        'res.company',
        string="Empresa",
        required=True,
        default=lambda self: self.env.company,
    )
    signature = fields.Binary(
        string="Firma del Empleador",
        help="Subí una imagen PNG o JPG de la firma del empleador.",
    )

    current_signature = fields.Binary(
        string="Firma actual",
        compute='_compute_current_signature',
    )

    @api.depends('company_id')
    def _compute_current_signature(self):
        for rec in self:
            rec.current_signature = rec.company_id.employer_signature

    def action_save(self):
        self.ensure_one()
        if not self.signature:
            raise UserError(_("Por favor ingresá una firma antes de guardar."))
        self.company_id.sudo().employer_signature = self.signature
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Firma guardada"),
                'message': _("La firma del empleador fue guardada correctamente."),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_clear(self):
        self.ensure_one()
        self.company_id.sudo().employer_signature = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Firma eliminada"),
                'message': _("La firma del empleador fue eliminada."),
                'type': 'warning',
                'sticky': False,
            },
        }
