import json

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
        self.validate_harvest_log_names()

    def get_harvest_log_names(self):
        if not self.harvest_log_names:
            return None
        try:
            names = json.loads(self.harvest_log_names)
        except ValueError:
            frappe.throw(_("harvest_log_names is not valid JSON."))
        if not isinstance(names, list):
            frappe.throw(_("harvest_log_names must be a JSON array of Harvest Log names."))
        return names

    def validate_harvest_log_names(self):
        """When the caller names exactly which logs this payment covers,
        confirm each one really belongs to this harvester and is still
        unpaid -- otherwise a stale client payload (or a correction payment
        reusing an old list) could silently re-claim an already-paid log, or
        one that belongs to someone else."""
        names = self.get_harvest_log_names()
        if not names:
            return
        rows = frappe.get_all(
            "Harvest Log",
            filters={"name": ["in", names]},
            fields=["name", "harvester_id", "paid"],
        )
        found = {r.name for r in rows}
        missing = set(names) - found
        if missing:
            frappe.throw(_("Harvest Log(s) not found: {0}").format(", ".join(sorted(missing))))
        for r in rows:
            if r.harvester_id != self.harvester_id:
                frappe.throw(_("Harvest Log {0} does not belong to {1}.").format(r.name, self.harvester_id))
            if r.paid:
                frappe.throw(_("Harvest Log {0} is already paid.").format(r.name))

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
        """Mark the harvest logs this payment covers as paid.

        Pickers are paid for buckets as soon as they're logged — pickup is a
        later, separate step (getting the coffee off the farm) and has no
        bearing on whether they've been paid for it.

        Prefers the exact log names the caller supplied (harvest_log_names)
        over re-deriving "unpaid logs for harvester+date": a harvester can
        carry unpaid buckets from an earlier, never-picked-up day, and
        getUnpaidBucketsForHarvester bundles those into today's total with
        no date filter of its own. Matching by date alone would then only
        mark TODAY's logs paid, leaving the older ones still unpaid and
        liable to be bundled into — and paid for — a second time later.
        Falls back to the by-date match when nothing was supplied (desk-
        created payments, or any other caller that doesn't set it).
        """
        names = self.get_harvest_log_names()
        if names:
            logs = [{"name": n} for n in names]
        else:
            logs = frappe.get_all(
                "Harvest Log",
                filters={"harvester_id": self.harvester_id, "date": self.date, "paid": 0},
                fields=["name"],
            )
        for log in logs:
            frappe.db.set_value("Harvest Log", log["name"] if isinstance(log, dict) else log.name, "paid", 1, update_modified=False)
        self.db_set("paid", 1, update_modified=False)

    def on_cancel(self):
        """Unmark harvest logs as paid on cancel — same targeting as on_submit."""
        names = self.get_harvest_log_names()
        if names:
            logs = [{"name": n} for n in names]
        else:
            logs = frappe.get_all(
                "Harvest Log",
                filters={"harvester_id": self.harvester_id, "date": self.date, "paid": 1},
                fields=["name"],
            )
        for log in logs:
            frappe.db.set_value("Harvest Log", log["name"] if isinstance(log, dict) else log.name, "paid", 0, update_modified=False)
        self.db_set("paid", 0, update_modified=False)


@frappe.whitelist()
def submit_coffee_payment(name):
    """Submit by name only.

    frappe.client.submit takes a doc dict and reconstructs it via
    frappe.get_doc(dict) -- for a dict holding only {doctype, name} that
    builds a brand-new, entirely blank in-memory document (every other field
    None) rather than loading the real record, so submitting it either fails
    validation outright or hits Frappe's own modified-timestamp conflict
    check. Loading by name first avoids that trap altogether.
    """
    frappe.get_doc("Coffee Payment", name).submit()
