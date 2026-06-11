import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocumentsDocument(models.Model):
    _inherit = 'documents.document'

    payroll_receipt_line_id = fields.Many2one(
        'hr.payroll.receipt.line',
        string="Recibo de nómina",
        readonly=True,
        ondelete='set null',
    )

    @api.model
    def action_sign_payroll_receipt_for(self, document_id, caller_uid=None):
        """Create a sign request and return the signing URL.

        Called via controller with SUPERUSER_ID to avoid ACL issues.
        caller_uid is the real uid of the user who clicked the button.
        Returns an act_url action pointing to the Sign signing page.
        """
        doc = self.sudo().browse(document_id)
        if not doc.exists():
            raise UserError(_("Documento no encontrado."))

        # Resolve employee - try multiple sources
        employee = False
        Employee = self.env['hr.employee'].sudo()

        # 1. From the payroll receipt line
        if doc.payroll_receipt_line_id:
            employee = doc.payroll_receipt_line_id.employee_id

        # 2. From res_model = hr.employee
        if not employee and doc.res_model == 'hr.employee' and doc.res_id:
            employee = Employee.browse(doc.res_id).exists()

        # 3. From the document owner (portal user linked to employee)
        if not employee and doc.owner_id:
            employee = Employee.search([('user_id', '=', doc.owner_id.id)], limit=1)

        # 4. From the document partner_id
        if not employee and doc.partner_id:
            employee = Employee.search([('work_contact_id', '=', doc.partner_id.id)], limit=1)

        # 5. From the caller uid (portal user clicking the button)
        if not employee and caller_uid:
            employee = Employee.search([('user_id', '=', caller_uid)], limit=1)

        if not employee:
            raise UserError(_("No se encontró el empleado asociado a este documento."))

        # Resolve signer partner (use caller_uid if provided, otherwise employee's user)
        if caller_uid:
            signer_user = self.env['res.users'].sudo().browse(caller_uid).exists()
            signer_partner = signer_user.partner_id if signer_user else False
        else:
            signer_user = employee.user_id
            signer_partner = signer_user.partner_id if signer_user else False

        # Fallback to employee's work contact if no partner found
        if not signer_partner:
            signer_partner = employee.work_contact_id

        if not signer_partner:
            raise UserError(_(
                "El empleado %s no tiene usuario ni contacto configurado.", employee.name,
            ))

        # Get PDF data from document
        attachment = doc.attachment_id
        pdf_data = attachment.datas if attachment else doc.datas
        if not pdf_data:
            raise UserError(_("No se encontró el archivo PDF del recibo."))

        # Create a NEW attachment copy for the sign template (independent from the original)
        sign_attachment = self.env['ir.attachment'].sudo().create({
            'name': doc.name or f'Recibo - {employee.name}.pdf',
            'datas': pdf_data,
            'mimetype': 'application/pdf',
            'res_model': 'sign.template',
            'res_id': 0,
        })

        # Roles and types
        signer_role = self.env['sign.item.role'].sudo().search(
            [('name', 'ilike', 'Employee')], limit=1,
        ) or self.env['sign.item.role'].sudo().search([], limit=1)

        sign_type = self.env['sign.item.type'].sudo().search(
            [('item_type', '=', 'signature')], limit=1,
        )

        sign_items = []
        if sign_type and signer_role:
            sign_items.append((0, 0, {
                'type_id': sign_type.id,
                'responsible_id': signer_role.id,
                'page': 1,
                'posX': 0.30,
                'posY': 0.87,
                'width': 0.22,
                'height': 0.05,
            }))

        doc_name = doc.name or f"Recibo - {employee.name}"
        receipt_line = doc.payroll_receipt_line_id

        # Create sign template with sign.document (Odoo 19 structure)
        # Do NOT set folder_id to avoid documents_sign creating new documents
        template = self.env['sign.template'].sudo().create({
            'name': doc_name,
            'document_ids': [(0, 0, {
                'attachment_id': sign_attachment.id,
                'sign_item_ids': sign_items,
            })],
        })

        # Create sign request (no email), link to original document via reference_doc
        sign_request = self.env['sign.request'].sudo().with_context(no_sign_mail=True).create({
            'template_id': template.id,
            'reference': doc_name,
            'request_item_ids': [(0, 0, {
                'role_id': signer_role.id,
                'partner_id': signer_partner.id,
            })],
        })
        sign_request.sudo().write({'reference_doc': f'documents.document,{doc.id}'})

        # Link to receipt line
        if receipt_line:
            receipt_line.write({
                'employee_sign_request_id': sign_request.id,
                'state': 'sent_to_sign',
            })

        # Build the direct signing URL
        request_item = sign_request.sudo().request_item_ids[0]
        sign_url = f'/sign/document/{sign_request.id}/{request_item.access_token}'

        _logger.info(
            "Sign request %s created for employee '%s', url=%s",
            sign_request.id, employee.name, sign_url,
        )

        return {
            'type': 'ir.actions.act_url',
            'url': sign_url,
            'target': 'self',
        }

