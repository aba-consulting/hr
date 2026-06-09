import base64
import io
import logging

from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrPayrollReceiptBatch(models.Model):
    _name = 'hr.payroll.receipt.batch'
    _description = 'Lote de Recibos de Sueldo'
    _order = 'date desc, id desc'

    name = fields.Char("Nombre", required=True)
    date = fields.Date("Período", required=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('signing', 'En firma'),
        ('signed', 'Firmado'),
        ('split', 'Spliteado'),
        ('done', 'Distribuido'),
    ], default='draft', tracking=True)

    source_pdf = fields.Binary("PDF Original", attachment=True)
    source_pdf_filename = fields.Char("Nombre archivo original")
    signed_pdf = fields.Binary("PDF Firmado", attachment=True)
    signed_pdf_filename = fields.Char("Nombre archivo firmado")

    sign_request_id = fields.Many2one(
        'sign.request', "Solicitud de firma", readonly=True, ondelete='set null',
    )
    sign_state = fields.Selection(related='sign_request_id.state', string="Estado firma")

    line_ids = fields.One2many('hr.payroll.receipt.line', 'batch_id', "Recibos")
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )

    document_tag_id = fields.Many2one(
        'documents.tag', "Etiqueta documento",
        default=lambda self: self.env.ref(
            'hr_payroll_receipts.documents_tag_payroll_receipt',
            raise_if_not_found=False,
        ),
    )
    document_folder_id = fields.Many2one(
        'documents.document', "Carpeta destino",
        domain="[('type', '=', 'folder')]",
        help="Carpeta de Documents donde depositar los recibos si el empleado "
             "no tiene carpeta propia.",
    )

    line_count = fields.Integer(compute='_compute_counts')
    unmatched_count = fields.Integer(compute='_compute_counts')

    @api.depends('line_ids', 'line_ids.employee_id', 'line_ids.state')
    def _compute_counts(self):
        for batch in self:
            batch.line_count = len(batch.line_ids)
            batch.unmatched_count = len(
                batch.line_ids.filtered(lambda l: not l.employee_id)
            )

    # ── Sign ──────────────────────────────────────────────────────────

    def action_send_to_sign(self):
        """Create a sign.template + sign.request for the source PDF."""
        self.ensure_one()
        if not self.source_pdf:
            raise UserError(_("Primero subí el PDF original."))

        attachment = self.env['ir.attachment'].create({
            'name': self.source_pdf_filename or f"{self.name}.pdf",
            'datas': self.source_pdf,
            'mimetype': 'application/pdf',
            'res_model': self._name,
            'res_id': self.id,
        })
        template = self.env['sign.template'].create({
            'name': f"Firma - {self.name}",
            'attachment_id': attachment.id,
        })

        role = self.env.ref('sign.sign_item_role_default', raise_if_not_found=False)
        if not role:
            role = self.env['sign.item.role'].search([], limit=1)
        if not role:
            raise UserError(_("No se encontró un rol de firma en Odoo Sign."))

        sign_request = self.env['sign.request'].create({
            'template_id': template.id,
            'reference': f"Firma - {self.name}",
            'request_item_ids': [Command.create({
                'partner_id': self.env.user.partner_id.id,
                'role_id': role.id,
            })],
        })

        self.write({
            'sign_request_id': sign_request.id,
            'state': 'signing',
        })

        if hasattr(sign_request, 'go_to_signable_document'):
            return sign_request.go_to_signable_document()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sign.request',
            'res_id': sign_request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_refresh_sign(self):
        """Check if sign request is completed and fetch signed PDF."""
        self.ensure_one()
        if not self.sign_request_id:
            return
        sr = self.sign_request_id
        if sr.state == 'signed':
            signed_data = False
            signed_name = f"{self.name}_firmado.pdf"
            # Try completed_document binary field
            if hasattr(sr, 'completed_document') and sr.completed_document:
                signed_data = sr.completed_document
            # Try completed_document_attachment_ids
            elif hasattr(sr, 'completed_document_attachment_ids'):
                att = sr.completed_document_attachment_ids[:1]
                if att:
                    signed_data = att.datas
                    signed_name = att.name
            if not signed_data:
                raise UserError(_(
                    "La firma se completó pero no se encontró el documento firmado. "
                    "Podés subirlo manualmente en el campo 'PDF Firmado'."
                ))
            self.write({
                'signed_pdf': signed_data,
                'signed_pdf_filename': signed_name,
                'state': 'signed',
            })
        elif sr.state == 'canceled':
            self.write({'state': 'draft', 'sign_request_id': False})

    def action_skip_sign(self):
        """Skip signing — use original PDF as-is."""
        self.ensure_one()
        if not self.source_pdf:
            raise UserError(_("Primero subí el PDF original."))
        self.write({
            'signed_pdf': self.source_pdf,
            'signed_pdf_filename': self.source_pdf_filename,
            'state': 'signed',
        })

    def action_upload_signed(self):
        """Confirm manually-uploaded signed PDF."""
        self.ensure_one()
        if not self.signed_pdf:
            raise UserError(_("Primero subí el PDF firmado."))
        self.state = 'signed'

    # ── Split ─────────────────────────────────────────────────────────

    def action_split(self):
        """Split the signed PDF into individual pages and auto-match employees."""
        self.ensure_one()
        pdf_data = self.signed_pdf or self.source_pdf
        if not pdf_data:
            raise UserError(_("No hay PDF para splitear."))

        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            raise UserError(_(
                "Falta la librería pypdf. Instalala con: pip install pypdf"
            ))

        reader = PdfReader(io.BytesIO(base64.b64decode(pdf_data)))

        # Build sequence → employee map
        employees = self.env['hr.employee'].search([
            ('payroll_sequence', '>', 0),
            ('company_id', '=', self.company_id.id),
        ], order='payroll_sequence')
        seq_map = {emp.payroll_sequence: emp for emp in employees}

        # Clear existing lines
        self.line_ids.unlink()

        lines_vals = []
        for idx, page in enumerate(reader.pages, start=1):
            writer = PdfWriter()
            writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)

            employee = seq_map.get(idx)
            emp_label = employee.name if employee else f"pag_{idx}"
            filename = f"recibo_{self.name.replace(' ', '_')}_{emp_label}.pdf"

            lines_vals.append(Command.create({
                'sequence': idx,
                'employee_id': employee.id if employee else False,
                'page_pdf': base64.b64encode(buf.getvalue()),
                'page_pdf_filename': filename,
                'state': 'matched' if employee else 'pending',
            }))

        self.write({
            'line_ids': lines_vals,
            'state': 'split',
        })

    # ── Distribute ────────────────────────────────────────────────────

    def action_distribute(self):
        """Deposit individual receipts into each employee's Documents folder."""
        self.ensure_one()
        unmatched = self.line_ids.filtered(lambda l: not l.employee_id)
        if unmatched:
            raise UserError(_(
                "Hay %(count)s recibo(s) sin empleado asignado. "
                "Asignalos antes de distribuir.",
                count=len(unmatched),
            ))

        lines_to_distribute = self.line_ids.filtered(
            lambda l: l.state in ('matched', 'pending')
        )
        if not lines_to_distribute:
            raise UserError(_("No hay recibos pendientes de distribuir."))

        # Ensure employees without folder get one generated (new employees or pre-config)
        employees_without_folder = self.line_ids.employee_id.filtered(
            lambda e: not e.sudo().hr_employee_folder_id
        )
        if employees_without_folder:
            employees_without_folder.sudo()._generate_employee_documents_folders()

        no_owner = []
        no_folder = []
        for line in lines_to_distribute:
            folder = self._get_employee_folder(line.employee_id)
            owner = self._get_employee_user(line.employee_id)
            emp_name = line.employee_id.name

            if not owner:
                no_owner.append(emp_name)
                _logger.warning(
                    "Recibo %s: no se encontró usuario para %s (user_id=%s)",
                    self.name, emp_name, line.employee_id.user_id,
                )
            if not folder:
                no_folder.append(emp_name)

            employee = line.employee_id
            partner = employee.work_contact_id
            # For "Shared with me" to work, access_ids must use the partner
            # of the actual user (portal or internal), not just the work contact
            portal_user = owner or employee.user_id
            access_partner = portal_user.partner_id if portal_user else partner

            doc_vals = {
                'name': f"Recibo {self.name} - {emp_name}",
                'datas': line.page_pdf,
                'type': 'binary',
                'res_model': 'hr.employee',
                'res_id': line.employee_id.id,
                'access_via_link': 'view',
                'access_internal': 'view',
                'is_access_via_link_hidden': False,
            }
            if folder:
                doc_vals['folder_id'] = folder.id
            if owner:
                doc_vals['owner_id'] = owner.id
            if partner:
                doc_vals['partner_id'] = partner.id
            if access_partner:
                doc_vals['access_ids'] = [Command.create({
                    'partner_id': access_partner.id,
                    'role': 'view',
                })]
            if self.document_tag_id:
                doc_vals['tag_ids'] = [Command.link(self.document_tag_id.id)]

            doc = self.env['documents.document'].sudo().create(doc_vals)
            doc.sudo().write({'payroll_receipt_line_id': line.id})
            line.write({
                'document_id': doc.id,
                'state': 'distributed',
            })

            # Notify the employee by email
            self._send_receipt_notification(line.employee_id)

        self.state = 'done'

        # Show warnings
        warnings = []
        if no_owner:
            warnings.append(_("Sin usuario portal (owner): %s", ', '.join(no_owner)))
        if no_folder:
            warnings.append(_("Sin carpeta encontrada: %s", ', '.join(no_folder)))
        if warnings:
            msg = _(
                "Documentos creados, pero con advertencias:\n%s\n\n"
                "Los empleados sin owner no verán el recibo en el portal. "
                "Asigná el owner manualmente en Documents.",
                '\n'.join(warnings),
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Distribución completada con advertencias'),
                    'message': msg,
                    'type': 'warning',
                    'sticky': True,
                },
            }

    def _get_employee_user(self, employee):
        """Find the portal or internal user for this employee."""
        # 1. Direct user link
        if employee.user_id:
            return employee.user_id
        # 2. Scan all Many2one → res.partner fields for a linked user
        for field_name, field in employee._fields.items():
            if field.type == 'many2one' and getattr(field, 'comodel_name', '') == 'res.partner':
                partner = employee[field_name]
                if partner and partner.user_ids:
                    return partner.user_ids[0]
        # 3. Search portal/internal user by work or private email
        emails = [email for email in (employee.work_email, employee.private_email) if email]
        if emails:
            user = self.env['res.users'].sudo().search([
                ('login', 'in', emails),
            ], limit=1)
            if user:
                return user
        # 4. Last resort: search portal user by employee name
        user = self.env['res.users'].sudo().search([
            ('name', '=', employee.name),
            ('share', '=', True),
        ], limit=1)
        return user or False

    def _get_employee_folder(self, employee):
        """Return the payroll subfolder for the employee.

        Looks up HR > [Employee name] > [subfolder from company config].
        Falls back to the employee folder, then to the batch default folder.
        """
        company = employee.company_id.sudo()
        hr_root = company.documents_employee_folder_id

        # 1. Ensure hr_employee_folder_id is linked (may be missing for old employees)
        employee_folder = employee.sudo().hr_employee_folder_id
        if not employee_folder and hr_root:
            employee_folder = self.env['documents.document'].sudo().search([
                ('type', '=', 'folder'),
                ('name', '=', employee.name),
                ('folder_id', '=', hr_root.id),
            ], limit=1)
            if employee_folder:
                employee.sudo().hr_employee_folder_id = employee_folder

        if not employee_folder:
            return self.document_folder_id

        # 2. Find the payroll subfolder by company config name
        subfolders_config = company.employee_subfolders or ''
        subfolder_names = [s.strip() for s in subfolders_config.split(',') if s.strip()]
        if subfolder_names:
            receipts_folder = self.env['documents.document'].sudo().search([
                ('type', '=', 'folder'),
                ('name', 'in', subfolder_names),
                ('folder_id', '=', employee_folder.id),
            ], limit=1)
            if receipts_folder:
                return receipts_folder
            # Not yet created — trigger generation
            employee.sudo()._generate_employee_documents_subfolders()
            receipts_folder = self.env['documents.document'].sudo().search([
                ('type', '=', 'folder'),
                ('name', 'in', subfolder_names),
                ('folder_id', '=', employee_folder.id),
            ], limit=1)
            if receipts_folder:
                return receipts_folder

        return employee_folder

    # ── Employee Sign ────────────────────────────────────────────────

    def action_send_employee_sign(self):
        """Send individual sign requests to each employee for their receipt."""
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_("Primero distribuí los recibos."))

        lines_to_sign = self.line_ids.filtered(
            lambda l: l.state == 'distributed' and not l.employee_sign_request_id
        )
        if not lines_to_sign:
            raise UserError(_("No hay recibos pendientes de firma de empleado."))

        role = self.env.ref('sign.sign_item_role_default', raise_if_not_found=False)
        if not role:
            role = self.env['sign.item.role'].search([], limit=1)
        if not role:
            raise UserError(_("No se encontró un rol de firma en Odoo Sign."))

        sign_type = self.env.ref('sign.sign_item_type_signature', raise_if_not_found=False)
        if not sign_type:
            sign_type = self.env['sign.item.type'].search(
                [('item_type', '=', 'signature')], limit=1,
            )

        no_email = []
        sent = []
        for line in lines_to_sign:
            employee = line.employee_id
            partner = self._get_employee_partner(employee)

            if not partner or not partner.email:
                no_email.append(employee.name)
                continue

            # Create attachment for this receipt
            attachment = self.env['ir.attachment'].create({
                'name': line.page_pdf_filename or f"recibo_{employee.name}.pdf",
                'datas': line.page_pdf,
                'mimetype': 'application/pdf',
            })

            # Create sign template + sign.document (Odoo 19 structure)
            template = self.env['sign.template'].create({
                'name': f"Recibo {self.name} - {employee.name}",
            })
            sign_doc = self.env['sign.document'].create({
                'template_id': template.id,
                'attachment_id': attachment.id,
            })

            # Add signature field at bottom of page
            if sign_type:
                self.env['sign.item'].create({
                    'document_id': sign_doc.id,
                    'type_id': sign_type.id,
                    'responsible_id': role.id,
                    'page': 1,
                    'posX': 35.0,
                    'posY': 85.0,
                    'width': 20.0,
                    'height': 5.0,
                })

            # Create and send sign request
            sign_request = self.env['sign.request'].create({
                'template_id': template.id,
                'reference': f"Firma Recibo {self.name} - {employee.name}",
                'request_item_ids': [Command.create({
                    'partner_id': partner.id,
                    'role_id': role.id,
                })],
            })

            # Try to send the request (email to employee)
            try:
                if hasattr(sign_request, 'action_sent'):
                    sign_request.action_sent()
            except Exception:
                _logger.info(
                    "Could not auto-send sign request %s (staging?), skipping.",
                    sign_request.id,
                )

            line.write({
                'employee_sign_request_id': sign_request.id,
                'state': 'sent_to_sign',
            })
            sent.append(employee.name)

        msg_parts = []
        if sent:
            msg_parts.append(_("Firma enviada a: %s", ', '.join(sent)))
        if no_email:
            msg_parts.append(_(
                "Sin email (no se envió): %s", ', '.join(no_email),
            ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Solicitudes de firma'),
                'message': '\n'.join(msg_parts),
                'type': 'warning' if no_email else 'success',
                'sticky': bool(no_email),
            },
        }

    def action_refresh_employee_signs(self):
        """Check status of all pending employee sign requests."""
        self.ensure_one()
        updated = 0
        for line in self.line_ids.filtered(lambda l: l.state == 'sent_to_sign'):
            sr = line.employee_sign_request_id
            if not sr:
                continue
            if sr.state == 'signed':
                vals = {'state': 'employee_signed'}
                # Try to get the signed document
                if hasattr(sr, 'completed_document') and sr.completed_document:
                    vals['signed_pdf'] = sr.completed_document
                    vals['signed_pdf_filename'] = f"recibo_{self.name}_{line.employee_id.name}_firmado.pdf"
                line.write(vals)
                updated += 1
        if updated:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Firmas actualizadas'),
                    'message': _("%s recibo(s) firmados.", updated),
                    'type': 'success',
                },
            }

    def _send_receipt_notification(self, employee):
        """Send an email to the employee notifying that their receipt is ready."""
        template = self.env.ref(
            'hr_payroll_receipts.mail_template_payroll_receipt_notify',
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning(
                "Template mail_template_payroll_receipt_notify no encontrado, "
                "no se enviará notificación a %s.", employee.name,
            )
            return
        partner = self._get_employee_partner(employee)
        if not partner or not partner.email:
            _logger.warning(
                "Empleado '%s' sin email, no se envió notificación.", employee.name,
            )
            return
        try:
            template.send_mail(partner.id, force_send=True)
        except Exception as e:
            _logger.warning(
                "Error al enviar notificación a '%s': %s", employee.name, e,
            )

    def _get_employee_partner(self, employee):
        """Find the partner with email to send sign request to."""
        # 1. Prioritize the portal/internal user partner because they sign with this identity
        user = self._get_employee_user(employee)
        if user and user.partner_id and user.partner_id.email:
            return user.partner_id
        # 2. Fallback to work contact
        if employee.work_contact_id and employee.work_contact_id.email:
            return employee.work_contact_id
        # 3. Fallback to work contact if no user found
        if employee.work_contact_id:
            return employee.work_contact_id
        return False

    # ── Misc ──────────────────────────────────────────────────────────

    def action_back_to_draft(self):
        """Reset batch to draft state."""
        self.ensure_one()
        self.line_ids.unlink()
        self.write({
            'state': 'draft',
            'signed_pdf': False,
            'signed_pdf_filename': False,
        })
