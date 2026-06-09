from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    payroll_sequence = fields.Integer(
        string="Orden en recibo",
        help="Número de orden/página que le corresponde al empleado "
             "en el PDF masivo de recibos de sueldo.",
    )
