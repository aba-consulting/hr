import base64
import io
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SignRequest(models.Model):
    _inherit = 'sign.request'

    signer_partner_id = fields.Many2one(
        'res.partner',
        string='Signer',
        compute='_compute_signer_partner_id',
        store=True,
        help='Primary signer for grouping purposes',
    )

    @api.depends('request_item_ids.partner_id')
    def _compute_signer_partner_id(self):
        for request in self:
            request.signer_partner_id = request.request_item_ids[:1].partner_id if request.request_item_ids else False

    def _get_signed_pdf_data(self):
        """Return the signed PDF binary from this completed sign request."""
        self.ensure_one()
        # Odoo 19: completed_document_attachment_ids is a Many2many of ir.attachment
        att = self.sudo().completed_document_attachment_ids[:1]
        if att:
            return att.datas
        return False

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') != 'signed':
            return res

        # 1. Batch employer signatures
        batches = self.env['hr.payroll.receipt.batch'].sudo().search([
            ('sign_request_id', 'in', self.ids),
            ('state', '=', 'signing'),
        ])
        for batch in batches:
            batch.action_refresh_sign()

        # 2. Employee receipt signatures (with receipt line)
        lines = self.env['hr.payroll.receipt.line'].sudo().search([
            ('employee_sign_request_id', 'in', self.ids),
            ('state', '=', 'sent_to_sign'),
        ])
        for line in lines:
            signed_data = line.employee_sign_request_id._get_signed_pdf_data()
            signed_filename = (
                f"recibo_{line.batch_id.name}_{line.employee_id.name}_firmado.pdf"
            )
            line_vals = {'state': 'employee_signed'}

            if signed_data:
                line_vals['signed_pdf'] = signed_data
                line_vals['signed_pdf_filename'] = signed_filename

            line.write(line_vals)

        # 3. Delete original document from reference_doc after signing
        # Use postcommit to avoid interfering with _sign() which uses reference_doc after write()
        doc_ids_to_delete = []
        for sign_req in self.sudo():
            try:
                ref = sign_req.reference_doc
                if ref and hasattr(ref, '_name') and ref._name == 'documents.document' and ref.id:
                    doc_ids_to_delete.append(ref.id)
            except Exception as e:
                _logger.warning("Could not resolve reference_doc for sign request %s: %s", sign_req.id, e)

        if doc_ids_to_delete:
            dbname = self.env.cr.dbname
            ids_copy = list(doc_ids_to_delete)

            def _delete_docs_after_commit():
                try:
                    from odoo.modules.registry import Registry
                    from odoo import SUPERUSER_ID
                    with Registry(dbname).cursor() as cr:
                        env = api.Environment(cr, SUPERUSER_ID, {})
                        docs = env['documents.document'].browse(ids_copy).exists()
                        for doc in docs:
                            _logger.info(
                                "Deleting original document %s from Documents after signing completed",
                                doc.id,
                            )
                        docs.unlink()
                except Exception as e:
                    _logger.warning("postcommit: could not delete documents %s: %s", ids_copy, e)

            self.env.cr.postcommit.add(_delete_docs_after_commit)

        return res

    def action_merge_signed_pdfs(self):
        """Merge signed PDFs from selected sign requests into a single PDF for download."""
        try:
            from pypdf import PdfWriter, PdfReader
        except ImportError:
            raise UserError(_("Falta la librería pypdf. Instalala con: pip install pypdf"))

        signed_requests = self.filtered(lambda r: r.state == 'signed')
        if not signed_requests:
            raise UserError(_("No hay documentos firmados en la selección."))

        writer = PdfWriter()
        merged_count = 0

        for req in signed_requests.sudo():
            # completed_document_ids -> sign.completed.document, field 'file' is the signed PDF binary
            completed_docs = req.completed_document_ids
            if not completed_docs:
                # fallback: trigger generation if not yet done
                try:
                    req._generate_completed_documents()
                    completed_docs = req.completed_document_ids
                except Exception as e:
                    _logger.warning("Could not generate completed document for sign.request %s: %s", req.id, e)

            pdf_data = None
            if completed_docs and completed_docs[:1].file:
                pdf_data = completed_docs[:1].file
            else:
                # last fallback: completed_document_attachment_ids (skip certificate — first att is the signed PDF)
                att = req.completed_document_attachment_ids.filtered(
                    lambda a: a.mimetype == 'application/pdf' and 'certificate' not in (a.name or '').lower()
                )[:1]
                if att and att.datas:
                    pdf_data = base64.b64decode(att.datas)

            if not pdf_data:
                _logger.warning("sign.request %s has no signed PDF, skipping.", req.id)
                continue
            try:
                reader = PdfReader(io.BytesIO(pdf_data if isinstance(pdf_data, bytes) else base64.b64decode(pdf_data)))
                for page in reader.pages:
                    writer.add_page(page)
                merged_count += 1
            except Exception as e:
                _logger.warning("Could not read PDF for sign.request %s: %s", req.id, e)

        if merged_count == 0:
            raise UserError(_("No se pudieron leer los PDFs firmados de los documentos seleccionados."))

        buf = io.BytesIO()
        writer.write(buf)
        merged_b64 = base64.b64encode(buf.getvalue()).decode()

        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'recibos_firmados.pdf',
            'datas': merged_b64,
            'mimetype': 'application/pdf',
            'res_model': 'sign.request',
            'res_id': self.ids[0],
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }
