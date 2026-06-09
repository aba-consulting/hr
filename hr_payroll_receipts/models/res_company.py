from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    employer_signature = fields.Binary(
        string="Firma del Empleador",
        attachment=False,
        help="Imagen de la firma del empleador para estampar en los recibos de sueldo.",
    )
