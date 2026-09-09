import frappe
from frappe.model.document import Document


class EmployeeFaceEnrollment(Document):
    def before_save(self):
        if not self.enrolled_by:
            self.enrolled_by = frappe.session.user
        if not self.enrolled_at:
            self.enrolled_at = frappe.utils.now()
