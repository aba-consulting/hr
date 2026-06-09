import logging

from odoo import api, models, Command

_logger = logging.getLogger(__name__)


class DocumentsDocument(models.Model):
    _inherit = 'documents.document'

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = self._inject_employee_folder_access_ids(vals_list)
        docs = super().create(vals_list)
        if not self.env.context.get('documents_hr_skip_notify'):
            docs._notify_employee_on_upload()
        return docs

    def _inject_employee_folder_access_ids(self, vals_list):
        """Inherit access_ids from the employee folder when creating a document without explicit access_ids.

        The standard only inherits access_ids if the document is created with access_ids in vals.
        For documents uploaded manually by HR (no access_ids), the employee would not have access.
        This method ensures the employee folder's access_ids are propagated to child documents.
        """
        Employee = self.env['hr.employee'].sudo()
        employee_folders = {
            emp.hr_employee_folder_id.id: emp.hr_employee_folder_id
            for emp in Employee.search([('hr_employee_folder_id', '!=', False)])
        }
        if not employee_folders:
            return vals_list

        updated = []
        for vals in vals_list:
            folder_id = vals.get('folder_id')
            has_access_ids = vals.get('access_ids') not in (None, False, [])
            doc_type = vals.get('type', 'binary')
            if folder_id and not has_access_ids and doc_type != 'folder' and folder_id in employee_folders:
                folder = employee_folders[folder_id]
                inherited = folder._get_inherited_access_ids_vals()
                if inherited:
                    vals = dict(vals)
                    vals['access_ids'] = [Command.create(v) for v in inherited]
            updated.append(vals)
        return updated

    def _notify_employee_on_upload(self):
        """Send email to the employee when a document is uploaded to their HR folder."""
        template = self.env.ref(
            'documents_hr_employee_access.mail_template_document_uploaded_to_employee',
            raise_if_not_found=False,
        )
        if not template:
            return

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
