# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Pays a picker for days they worked but did not collect their money on the day.
#
# The app's Coffee Payment covers the payment date's own buckets only (see
# CoffeePayment.validate_harvest_log_names); anything older is paid here, one
# row per unpaid Harvest Log, so each bucket is paid exactly once and the
# exact logs are ticked paid on submit. It is its own payment record rather
# than a Coffee Payment because a picker is often paid for today in the app
# AND for earlier days here, and Coffee Payment allows one per harvester per day.

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class CoffeeBackdatedPayment(Document):
	def validate(self):
		if flt(self.rate) <= 0:
			frappe.throw(_("Rate per bucket must be greater than 0."))
		if not self.items:
			frappe.throw(_("Add the unpaid buckets to pay - use Get Unpaid Days."))
		logs = {
			r.name: r for r in frappe.get_all(
				"Harvest Log",
				filters={"name": ["in", [i.harvest_log for i in self.items]]},
				fields=["name", "harvester_id", "date", "block", "bucket_count", "paid"],
			)
		}
		taken = _logs_in_other_payments([i.harvest_log for i in self.items], self.name)
		pays_from = _pays_from()
		seen = set()
		for i in self.items:
			log = logs.get(i.harvest_log)
			if not log:
				frappe.throw(_("Row {0}: Harvest Log {1} not found.").format(i.idx, i.harvest_log))
			if i.harvest_log in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(i.idx, i.harvest_log))
			seen.add(i.harvest_log)
			if log.harvester_id != self.harvester_id:
				frappe.throw(_("Row {0}: {1} belongs to {2}, not {3}.").format(i.idx, log.name, log.harvester_id, self.harvester_id))
			if log.paid:
				frappe.throw(_("Row {0}: {1} ({2}) is already paid.").format(i.idx, log.name, log.date))
			if getdate(log.date) < getdate(pays_from):
				frappe.throw(_("Row {0}: {1} was picked on {2}, before pickers were paid in the system ({3}); that day was paid outside it.").format(
					i.idx, log.name, log.date, pays_from))
			if getdate(log.date) >= getdate(self.date):
				frappe.throw(_("Row {0}: {1} was picked on {2}. Only days before {3} are paid here; the day's own buckets are paid from the app.").format(
					i.idx, log.name, log.date, self.date))
			if i.harvest_log in taken:
				frappe.throw(_("Row {0}: {1} is already on {2}.").format(i.idx, log.name, taken[i.harvest_log]))
			i.log_date, i.block, i.bucket_count = log.date, log.block, log.bucket_count
			i.amount = flt(log.bucket_count) * flt(self.rate)
		self.total_buckets = sum(i.bucket_count or 0 for i in self.items)
		self.total_payment = flt(self.total_buckets) * flt(self.rate)

	def on_submit(self):
		for i in self.items:
			frappe.db.set_value("Harvest Log", i.harvest_log, "paid", 1, update_modified=False)

	def before_cancel(self):
		for doctype, name in (("Payment Entry", self.get("payment_entry")), ("Journal Entry", self.get("journal_entry"))):
			if name and frappe.db.get_value(doctype, name, "docstatus") == 1:
				frappe.throw(
					_("This payment is posted in {0} {1}. Cancel that first.").format(doctype, name),
					title=_("Already posted"),
				)

	def on_cancel(self):
		for i in self.items:
			frappe.db.set_value("Harvest Log", i.harvest_log, "paid", 0, update_modified=False)


def _logs_in_other_payments(names, current):
	"""Logs already on another draft or submitted backdated payment."""
	if not names:
		return {}
	rows = frappe.db.sql(
		"""SELECT i.harvest_log, p.name FROM `tabCoffee Backdated Payment Item` i
		JOIN `tabCoffee Backdated Payment` p ON p.name = i.parent
		WHERE i.harvest_log IN %(n)s AND p.docstatus < 2 AND p.name != %(c)s""",
		{"n": tuple(names), "c": current or ""},
	)
	return dict(rows)


def _pays_from():
	"""First day a log can be owed: the season start, or the first Coffee Payment
	of the season if later. Days before pickers were paid in the system were paid
	outside it (cash), but their logs were never ticked paid - without this the
	whole of September 2026 shows as owed."""
	season = str(frappe.db.get_value("Coffee Season", {"is_active": 1}, "start_date") or "2000-01-01")
	first = frappe.db.get_value("Coffee Payment", {"docstatus": 1, "date": [">=", season]}, "date", order_by="date asc")
	return max(season, str(first)) if first else season


@frappe.whitelist()
def get_unpaid_logs(harvester_id, date=None):
	"""The harvester's unpaid buckets from days before `date` - only days they
	actually worked, since a log exists only for a day they brought in buckets."""
	date = date or frappe.utils.nowdate()
	logs = frappe.get_all(
		"Harvest Log",
		filters={"harvester_id": harvester_id, "paid": 0, "date": ["between", [_pays_from(), frappe.utils.add_days(date, -1)]]},
		fields=["name", "date", "block", "bucket_count"],
		order_by="date asc, creation asc",
		limit_page_length=0,
	)
	taken = _logs_in_other_payments([l.name for l in logs], None)
	return {
		"logs": [l for l in logs if l.name not in taken],
		"rate": frappe.db.get_value("Coffee Season", {"is_active": 1}, "default_bucket_rate") or 0,
	}
