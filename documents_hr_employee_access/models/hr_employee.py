from odoo import api, models, Command


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _generate_employee_documents_folders(self, skip_subfolders=False):
        super()._generate_employee_documents_folders(skip_subfolders=skip_subfolders)
        self._fix_employee_folder_access()

    def _fix_employee_folder_access(self):
        """Set correct owner, restrict public access and grant access to the employee
        and HR managers on newly created employee folders."""
        hr_manager_group = self.env.ref('hr.group_hr_manager', raise_if_not_found=False)

        for employee in self.filtered('hr_employee_folder_id'):
            folder = employee.sudo().hr_employee_folder_id
            user = employee.user_id

            access_ids_vals = [Command.clear()]

            if user and user.partner_id:
                access_ids_vals.append(Command.create({
                    'partner_id': user.partner_id.id,
                    'role': 'view',
                }))

            if hr_manager_group:
                employee_partner_id = user.partner_id.id if user else False
                for hr_user in hr_manager_group.all_user_ids.filtered(
                    lambda u: u.active and not u.share and u.partner_id
                ):
                    if hr_user.partner_id.id != employee_partner_id:
                        access_ids_vals.append(Command.create({
                            'partner_id': hr_user.partner_id.id,
                            'role': 'edit',
                        }))

            folder.write({
                'owner_id': user.id if user else False,
                'access_via_link': 'none',
                'access_internal': 'view',
                'is_access_via_link_hidden': True,
                'access_ids': access_ids_vals,
            })
