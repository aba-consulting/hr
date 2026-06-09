/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { DocumentsAction } from "@documents/views/action/documents_action";

// Patch DocumentsAction (topbar with Acciones/Descargar) to add sign button
patch(DocumentsAction.prototype, {
    onSignPayrollReceipt() {
        const records = this.props.targetRecords;
        if (!records || !records.length) return;
        const docId = records[0].resId || records[0].data?.id;
        if (!docId) return;
        window.location.href = `/hr_payroll_receipts/sign/${docId}`;
    },
});
