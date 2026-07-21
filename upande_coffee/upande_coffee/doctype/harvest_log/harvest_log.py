import frappe
from frappe.model.document import Document


class HarvestLog(Document):
    def before_insert(self):
        # Preserve the date the scan was actually taken. Offline scans are
        # queued on the device and may upload hours or a day later — the
        # Kahawa Trail app sends the original harvest date, so only fall back
        # to today when no date was supplied (e.g. a live desk entry).
        if not self.date:
            self.date = frappe.utils.today()
        self.bucket_count = 1
        # Fetch national_id and employee_id from harvester
        if self.harvester_id:
            harvester = frappe.get_doc("Harvester", self.harvester_id)
            self.national_id = harvester.national_id
            self.employee_id = harvester.employee_id

    def validate(self):
        if not self.harvester_id:
            frappe.throw("Harvester is required.")
        if not self.block:
            frappe.throw("Block (warehouse) is required.")
