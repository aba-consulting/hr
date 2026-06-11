from odoo import api, fields, models


class HrPayrollReceiptLine(models.Model):
    _name = 'hr.payroll.receipt.line'
    _description = 'Recibo de Sueldo Individual'
    _order = 'sequence, id'

    batch_id = fields.Many2one(
        'hr.payroll.receipt.batch', required=True, ondelete='cascade',
    )
    sequence = fields.Integer("Página")
    employee_id = fields.Many2one('hr.employee', "Empleado")
    page_pdf = fields.Binary("PDF", attachment=True)
    page_pdf_filename = fields.Char()
    document_id = fields.Many2one('documents.document', "Documento", readonly=True)
    employee_sign_request_id = fields.Many2one(
        'sign.request', "Solicitud firma empleado", readonly=True, ondelete='set null',
    )
    employee_sign_state = fields.Selection(
        related='employee_sign_request_id.state', string="Estado firma",
    )
    signed_pdf = fields.Binary("PDF Firmado", attachment=True)
    signed_pdf_filename = fields.Char()
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('matched', 'Asignado'),
        ('distributed', 'Distribuido'),
        ('sent_to_sign', 'Enviado a firmar'),
        ('employee_signed', 'Firmado'),
    ], default='pending')

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id and self.state == 'pending':
            self.state = 'matched'
        elif not self.employee_id and self.state == 'matched':
            self.state = 'pending'
