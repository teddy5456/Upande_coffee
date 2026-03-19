import frappe
from frappe import _
from frappe.model.document import Document


class CoffeePayment(Document):
    def validate(self):
        if not self.total_buckets or self.total_buckets <= 0:
            frappe.throw(_("Total buckets must be greater than 0."))
        if not self.rate or self.rate <= 0:
            frappe.throw(_("Rate must be greater than 0."))
        self.total_payment = self.total_buckets * self.rate

    def on_submit(self):
        """Mark harvest logs for this harvester on this date as paid."""
        harvester = self.harvester_id
        date = self.date
        logs = frappe.get_all(
            "Harvest Log",
            filters={"harvester_id": harvester, "date": date, "paid": 0, "picked_up": 1},
            fields=["name"],
        )
        for log in logs:
            frappe.db.set_value("Harvest Log", log.name, "paid", 1, update_modified=False)

    def on_cancel(self):
        """Unmark harvest logs as paid on cancel."""
        harvester = self.harvester_id
        date = self.date
        logs = frappe.get_all(
            "Harvest Log",
            filters={"harvester_id": harvester, "date": date, "paid": 1},
            fields=["name"],
        )
        for log in logs:
            frappe.db.set_value("Harvest Log", log.name, "paid", 0, update_modified=False)
