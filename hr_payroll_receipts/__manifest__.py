{
    'name': 'Recibos de Sueldo',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Payroll',
    'author':'ABA Consulting',
    'summary': 'Split y distribución masiva de recibos de sueldo via Documents',
    'description': """
Gestión masiva de recibos de sueldo
====================================
* Upload de PDF masivo (un recibo por página)
* Firma digital via Odoo Sign
* Split automático por página
* Asignación a empleados por orden predefinido
* Distribución a carpetas de Documents
* Visualización portal para empleados
    """,
    'depends': ['hr', 'hr_payroll', 'documents_hr', 'sign'],
    'data': [
        'security/hr_payroll_receipt_security.xml',
        'security/ir.model.access.csv',
        'data/documents_tag_data.xml',
        'data/mail_template_data.xml',
        'views/hr_payroll_receipt_batch_views.xml',
        'views/hr_employee_views.xml',
        'views/documents_views.xml',
        'views/sign_request_views.xml',
        'views/employer_signature_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'hr_payroll_receipts/static/src/js/sign_payroll_receipt.js',
            'hr_payroll_receipts/static/src/xml/sign_payroll_receipt.xml',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
