import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class DocumentsDocument(models.Model):
    _inherit = 'documents.document'

    @api.model_create_multi
    def create(self, vals_list):
        docs = super().create(vals_list)
        if not self.env.context.get('documents_hr_skip_notify'):
            docs._notify_employee_on_upload()
        return docs

    def _notify_employee_on_upload(self):
        """Send email to the employee when a document is uploaded to their HR folder."""
        template = self.env.ref(
            'documents_hr_employee_access.mail_template_document_uploaded_to_employee',
            raise_if_not_found=False,
        )
        if not template:
            return

        Employee = self.env['hr.employee'].sudo()

        for doc in self.filtered(lambda d: d.type != 'folder'):
            employee = self._get_employee_from_folder(doc)
            if not employee or not employee.work_email:
                continue
            try:
                template.send_mail(doc.id, force_send=False, email_values={
                    'email_to': employee.work_email,
                })
            except Exception:
                _logger.warning(
                    "documents_hr_employee_access: failed to notify employee %s for document %s",
                    employee.name, doc.id, exc_info=True,
                )

    def _get_employee_from_folder(self, doc):
        """Return the employee whose hr_employee_folder_id matches the doc's folder (or ancestor)."""
        Employee = self.env['hr.employee'].sudo()

        folder = doc.folder_id
        while folder:
            employee = Employee.search([('hr_employee_folder_id', '=', folder.id)], limit=1)
            if employee:
                return employee
            folder = folder.folder_id
        return False
