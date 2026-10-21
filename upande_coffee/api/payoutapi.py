# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Daily posting of picker pay out of the week's float.
#
# A submitted Coffee Harvest Plan raises a Payment Request for the week's
# estimated picking cash; accounts settle it with an Internal Transfer Payment
# Entry from the bank into a float account (M-Pesa / petty cash). Pickers are
# then paid out of that float all week as Coffee Payments (and Coffee Backdated
# Payments for days they didn't collect). This posts each pay day's payments
# as ONE Journal Entry: credit the float, debit the
# plan's picking-wages account split by block cost centre.
#
# Journal Entry, not Payment Entry: a Pay-type Payment Entry needs a party, and
# pickers are Harvesters, not a Party Type. Paying the plan's claimant
# (Employee) would park every shilling as an employee advance that nothing
# ever clears.

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def post_daily(upto=None):
	"""Scheduler entry point: post every pay day up to `upto` (default today)
	that still has unposted payments. Catches up missed days by itself."""
	out = []
	days = set()
	for doctype in PAYMENT_DOCTYPES:
		days.update(frappe.get_all(
			doctype,
			filters={"docstatus": 1, "journal_entry": ["is", "not set"], "date": ["<=", upto or nowdate()]},
			pluck="date", distinct=True,
		))
	for day in sorted(days):
		try:
			out.append(post_day(day))
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Coffee pay posting failed for {day}")
	return out


@frappe.whitelist()
def post_daily_payments(upto=None):
	frappe.only_for(["Accounts Manager", "System Manager"])
	return post_daily(upto)


# Coffee Payment = the day's own buckets, paid from the app; Coffee Backdated
# Payment = earlier days a picker didn't collect. Both come out of the same float.
PAYMENT_DOCTYPES = ("Coffee Payment", "Coffee Backdated Payment")


def _unposted(day):
	payments = frappe.get_all(
		"Coffee Payment",
		filters={"docstatus": 1, "journal_entry": ["is", "not set"], "date": day},
		fields=["name", "harvester_id", "date", "rate", "total_payment", "total_buckets", "harvest_log_names"],
	)
	for p in payments:
		p.doctype = "Coffee Payment"
	for b in frappe.get_all(
		"Coffee Backdated Payment",
		filters={"docstatus": 1, "journal_entry": ["is", "not set"], "date": day},
		fields=["name", "harvester_id", "date", "rate", "total_payment", "total_buckets"],
	):
		b.doctype = "Coffee Backdated Payment"
		b.harvest_log_names = json.dumps(frappe.get_all(
			"Coffee Backdated Payment Item", filters={"parent": b.name}, pluck="harvest_log"))
		payments.append(b)
	return payments


def post_day(day):
	day = getdate(day)
	payments = _unposted(day)
	if not payments:
		return {"date": str(day), "skipped": "nothing to post"}

	plan = frappe.db.get_value(
		"Coffee Harvest Plan",
		{"docstatus": 1, "from_date": ["<=", day], "to_date": [">=", day], "payment_request": ["is", "set"]},
		["name", "company", "payment_request", "expense_account", "expense_claim_type"],
		as_dict=True,
	)
	if not plan:
		return {"date": str(day), "skipped": "no approved harvest plan with a payment request covers this day"}
	# plans saved without one fall back to the picking-wages account of their Expense Claim Type
	plan.expense_account = plan.expense_account or frappe.db.get_value(
		"Expense Claim Account",
		{"parent": plan.expense_claim_type or "Coffee Harvesting Labour", "company": plan.company},
		"default_account",
	)
	float_account = frappe.db.get_value(
		"Payment Entry",
		{"payment_request": plan.payment_request, "payment_type": "Internal Transfer", "docstatus": 1},
		"paid_to", order_by="posting_date desc, creation desc",
	)
	if not float_account:
		return {"date": str(day), "skipped": f"{plan.payment_request} has no submitted Internal Transfer yet"}
	if not plan.expense_account:
		return {"date": str(day), "skipped": f"{plan.name} has no expense account"}

	by_cc = _split_by_cost_center(payments, plan)
	total = flt(sum(by_cc.values()), 2)
	account_type = frappe.db.get_value("Account", float_account, "account_type")

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = {"Bank": "Bank Entry", "Cash": "Cash Entry"}.get(account_type, "Journal Entry")
	je.company = plan.company
	je.posting_date = day
	je.cheque_no = f"COFFEE-PAY-{day}"
	je.cheque_date = day
	je.user_remark = (
		f"Coffee picker pay for {day}: {len(payments)} payments, "
		f"{sum(p.total_buckets or 0 for p in payments)} buckets, out of the {plan.payment_request} float ({plan.name})."
	)
	for cc, amount in by_cc.items():
		je.append("accounts", {"account": plan.expense_account, "cost_center": cc,
			"debit_in_account_currency": flt(amount, 2)})
	je.append("accounts", {"account": float_account, "credit_in_account_currency": total})
	je.insert()
	je.submit()

	for p in payments:
		frappe.db.set_value(p.doctype, p.name, "journal_entry", je.name, update_modified=False)
	return {"date": str(day), "journal_entry": je.name, "amount": total, "payments": len(payments)}


def _split_by_cost_center(payments, plan):
	"""KES per cost centre, following each payment's harvest logs to their
	block (blocks and cost centres share names on Kaitet). Payments made
	before harvest_log_names was always recorded fall back to that harvester's
	logs on the pay day, then their most recent earlier ones. Whatever still
	can't be traced lands on the company default, else the plan's first block,
	so the entry always balances."""
	block_cc = dict(frappe.get_all(
		"Coffee Harvest Plan Block", filters={"parent": plan.name}, fields=["block", "cost_center"], as_list=True))
	default_cc = frappe.get_cached_value("Company", plan.company, "cost_center") or next(iter(block_cc.values()), None)

	out = {}
	for p in payments:
		names = json.loads(p.harvest_log_names) if p.harvest_log_names else []
		if names:
			logs = frappe.get_all("Harvest Log", filters={"name": ["in", names]}, fields=["block", "bucket_count"])
		else:
			logs = frappe.db.sql(
				"""SELECT block, bucket_count FROM `tabHarvest Log`
				WHERE harvester_id = %(h)s AND date <= %(d)s
				ORDER BY date = %(d)s DESC, date DESC LIMIT 50""",
				{"h": p.harvester_id, "d": p.date}, as_dict=True)
		left = flt(p.total_payment)
		for log in logs:
			if left <= 0:
				break
			cc = block_cc.get(log.block) or (log.block if frappe.db.exists("Cost Center", log.block) else default_cc)
			amount = min(left, flt(log.bucket_count) * flt(p.rate))
			out[cc] = out.get(cc, 0) + amount
			left -= amount
		if left > 0:
			out[default_cc] = out.get(default_cc, 0) + left
	return out
