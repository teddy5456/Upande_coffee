import frappe
from frappe import _
from frappe.model.document import Document


class DailyMoistureReading(Document):
    def validate(self):
        if self.moisture_percentage <= 0 or self.moisture_percentage > 100:
            frappe.throw(_("Moisture percentage must be between 0 and 100."))
        # Auto-fill debes from the drying table
        if self.drying_table:
            debes = frappe.db.get_value("Drying Table", self.drying_table, "current_debes") or 0
            self.debes = debes
        if not self.read_by:
            self.read_by = frappe.session.user
