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
        self.validate_duplicate_payment()

    def validate_duplicate_payment(self):
        """One payment per harvester per date. Every creation path (desk form,
        API/mobile) goes through this same validate(), so the check can't be
        bypassed by skipping the desk UI."""
        if not (self.harvester_id and self.date):
            return
        duplicate = frappe.db.exists(
            "Coffee Payment",
            {
                "harvester_id": self.harvester_id,
                "date": self.date,
                "docstatus": ["!=", 2],
                "name": ["!=", self.name or ""],
            },
        )
        if duplicate:
            frappe.throw(
                _("{0} has already been paid for {1} in {2}. Cancel that payment first if this is a correction.").format(
                    self.harvester_id, self.date, duplicate
                ),
                title=_("Duplicate Payment"),
            )

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
        self.db_set("paid", 1, update_modified=False)

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
        self.db_set("paid", 0, update_modified=False)
