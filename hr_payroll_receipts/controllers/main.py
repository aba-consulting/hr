from odoo import http, SUPERUSER_ID
from odoo.http import request

try:
    from odoo.addons.sign.controllers.main import Sign as SignController
    _SIGN_AVAILABLE = True
except ImportError:
    _SIGN_AVAILABLE = False


class PayrollReceiptController(http.Controller):

    @http.route('/hr_payroll_receipts/sign/<int:document_id>', type='http', auth='user', website=True)
    def sign_payroll_receipt(self, document_id, **kwargs):
        """Create a sign request for the given document and redirect to the signing page."""
        # Keep the real user uid to resolve employee, then run with superuser
        real_uid = request.env.uid or request.uid
        env = request.env(user=SUPERUSER_ID)
        action = env['documents.document'].action_sign_payroll_receipt_for(document_id, caller_uid=real_uid)
        sign_url = action.get('url', '/')
        return request.redirect(sign_url)


if _SIGN_AVAILABLE:
    class SignControllerInherit(SignController):
        """Override the Sign controller to elevate privileges during the sign call.

        Third-party modules (e.g. those defining ai.agent.source) can trigger
        access-right checks on models that portal users cannot read. By temporarily
        running the request under SUPERUSER we bypass those checks while keeping
        the actual sign.request.item identity correct (the enterprise code still
        calls with_user(sign_user) internally).
        """

        @http.route([
            '/sign/sign/<int:sign_request_id>/<token>',
            '/sign/sign/<int:sign_request_id>/<token>/<sms_token>',
        ], type='jsonrpc', auth='public')
        def sign(self, sign_request_id, token, sms_token=False, signature=None, **kwargs):
            original_uid = request.uid
            try:
                # Elevate request environment to bypass third-party ACL checks
                request.update_env(user=SUPERUSER_ID)
                return super().sign(
                    sign_request_id, token,
                    sms_token=sms_token, signature=signature, **kwargs,
                )
            finally:
                # Always restore the original session user
                request.update_env(user=original_uid)
