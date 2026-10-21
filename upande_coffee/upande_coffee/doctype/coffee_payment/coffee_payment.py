import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


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
            fields=["name", "harvester_id", "paid", "date"],
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
            # A payment covers its own date's buckets only; older unpaid days
            # are settled separately, never bundled into today's payment.
            if getdate(r.date) != getdate(self.date):
                frappe.throw(
                    _("Harvest Log {0} is from {1}; this payment is for {2}.").format(r.name, r.date, self.date),
                    title=_("Today only"),
                )

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
        later, separate step and has no bearing on payment.

        Uses the exact log names the caller supplied (harvest_log_names) when
        present. Otherwise takes the harvester's unpaid logs oldest first, up
        to total_buckets: a picker who skipped pay on the 10th and collects on
        the 12th is paid for both days, and matching "logs on the pay date"
        only would leave the 10th's logs unpaid -- to be offered and paid a
        second time on the next scan (355 buckets on Kaitet by 2026-10-14).
        The chosen names are written back so cancel reverses exactly them.
        """
        names = self.get_harvest_log_names()
        if not names:
            names = self.oldest_unpaid_logs()
            self.db_set("harvest_log_names", json.dumps(names), update_modified=False)
        for name in names:
            frappe.db.set_value("Harvest Log", name, "paid", 1, update_modified=False)
        self.db_set("paid", 1, update_modified=False)

    def oldest_unpaid_logs(self):
        logs = frappe.get_all(
            "Harvest Log",
            filters={"harvester_id": self.harvester_id, "paid": 0, "date": ["<=", self.date]},
            fields=["name", "bucket_count"],
            order_by="date asc, creation asc",
            limit_page_length=0,
        )
        names, left = [], self.total_buckets
        for log in logs:
            if left <= 0:
                break
            names.append(log.name)
            left -= log.bucket_count or 0
        return names

    def before_cancel(self):
        for doctype, name in (("Payment Entry", self.get("payment_entry")), ("Journal Entry", self.get("journal_entry"))):
            if name and frappe.db.get_value(doctype, name, "docstatus") == 1:
                frappe.throw(
                    _("This payment is posted in {0} {1}. Cancel that first.").format(doctype, name),
                    title=_("Already posted"),
                )

    def on_cancel(self):
        """Unmark exactly the logs this payment marked paid."""
        names = self.get_harvest_log_names()
        if names is None:
            names = frappe.get_all(
                "Harvest Log",
                filters={"harvester_id": self.harvester_id, "date": self.date, "paid": 1},
                pluck="name",
            )
        for name in names:
            frappe.db.set_value("Harvest Log", name, "paid", 0, update_modified=False)
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
