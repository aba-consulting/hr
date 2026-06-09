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
            att = self.env['ir.attachment'].sudo().search([
                ('name', '=', 'employer_signature_payroll'),
                ('res_model', '=', 'res.company'),
                ('res_id', '=', rec.company_id.id),
            ], limit=1)
            rec.current_signature = att.datas if att else False

    def _save_signature_attachment(self, company, datas):
        att = self.env['ir.attachment'].sudo().search([
            ('name', '=', 'employer_signature_payroll'),
            ('res_model', '=', 'res.company'),
            ('res_id', '=', company.id),
        ], limit=1)
        if att:
            att.write({'datas': datas})
        else:
            self.env['ir.attachment'].sudo().create({
                'name': 'employer_signature_payroll',
                'datas': datas,
                'mimetype': 'image/png',
                'res_model': 'res.company',
                'res_id': company.id,
            })

    def _clear_signature_attachment(self, company):
        att = self.env['ir.attachment'].sudo().search([
            ('name', '=', 'employer_signature_payroll'),
            ('res_model', '=', 'res.company'),
            ('res_id', '=', company.id),
        ], limit=1)
        att.unlink()

    def action_save(self):
        self.ensure_one()
        if not self.signature:
            raise UserError(_("Por favor ingresá una firma antes de guardar."))
        self._save_signature_attachment(self.company_id, self.signature)
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
        self._clear_signature_attachment(self.company_id)
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
