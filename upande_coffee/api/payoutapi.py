# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Posting picker pay out of the week's float.
#
# A submitted Coffee Harvest Plan raises a Payment Request for the week's
# estimated picking cash; accounts settle it with an Internal Transfer Payment
# Entry from the bank into a float account (M-Pesa / petty cash). Pickers are
# paid out of that float as Coffee Payments (the day's own buckets, from the
# app) and Coffee Backdated Payments (earlier days they didn't collect).
#
# Each pay day posts as:
#   1. accrual Journal Entries (in parts of 60 harvesters) -- debit the plan's
#      picking-wages account by block cost centre, credit Coffee Pickers Payable
#      with one line per harvester (Party Type "Harvester") for that day's pay;
#   2. ONE Payment Entry per payment -- Pay, party = the harvester, from the
#      float into Coffee Pickers Payable, allocated against that Journal Entry.
# Every harvester's payable nets to zero, the wage cost sits on the right
# block, and every cash hand-out is its own document.
#
# Runs hourly and in batches (a pay day is ~300 payments), so a backlog --
# e.g. a float transfer made days late -- catches up by itself.

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

# Coffee Payment = the day's own buckets, paid from the app; Coffee Backdated
# Payment = earlier days a picker didn't collect. Both come out of the same float.
PAYMENT_DOCTYPES = ("Coffee Payment", "Coffee Backdated Payment")
PICKERS_PAYABLE = "Coffee Pickers Payable"
BATCH = 150  # Payment Entries per hourly run (~1 s each)
DAYS_PER_RUN = 5
# ERPNext background-queues Journal Entries over 100 lines; ~60 harvester lines
# plus the block cost-centre lines keeps each accrual part under that.
HARVESTERS_PER_JE = 60


def post_daily(upto=None):
	"""Scheduler entry point: accrue every unposted pay day up to `upto`
	(default today), then pay out up to BATCH payments against those accruals."""
	out = []
	days = set()
	for doctype in PAYMENT_DOCTYPES:
		days.update(frappe.get_all(
			doctype,
			filters={"docstatus": 1, "journal_entry": ["is", "not set"], "date": ["<=", upto or nowdate()]},
			pluck="date", distinct=True,
		))
	for day in sorted(days)[:DAYS_PER_RUN]:
		try:
			out.append(accrue_day(day))
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Coffee pay accrual failed for {day}")
	out.append({"payment_entries": pay_out(BATCH)})
	return out


@frappe.whitelist()
def post_daily_payments(upto=None):
	frappe.only_for(["Accounts Manager", "System Manager"])
	return post_daily(upto)


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


def _plan_for(day):
	"""The approved harvest plan covering `day`, its float account and wages account."""
	plan = frappe.db.get_value(
		"Coffee Harvest Plan",
		{"docstatus": 1, "from_date": ["<=", day], "to_date": [">=", day], "payment_request": ["is", "set"]},
		["name", "company", "payment_request", "expense_account", "expense_claim_type"],
		as_dict=True,
	)
	if not plan:
		return None, "no approved harvest plan with a payment request covers this day"
	# plans saved without one fall back to the picking-wages account of their Expense Claim Type
	plan.expense_account = plan.expense_account or frappe.db.get_value(
		"Expense Claim Account",
		{"parent": plan.expense_claim_type or "Coffee Harvesting Labour", "company": plan.company},
		"default_account",
	)
	plan.float_account = frappe.db.get_value(
		"Payment Entry",
		{"payment_request": plan.payment_request, "payment_type": "Internal Transfer", "docstatus": 1},
		"paid_to", order_by="posting_date desc, creation desc",
	)
	if not plan.float_account:
		return None, f"{plan.payment_request} has no submitted Internal Transfer yet"
	if not plan.expense_account:
		return None, f"{plan.name} has no expense account"
	plan.payable_account = pickers_payable_account(plan.company)
	return plan, None


def accrue_day(day):
	"""Accrue the day in parts of HARVESTERS_PER_JE harvesters: ERPNext queues
	any Journal Entry over 100 lines for background submission, and a pay day
	has ~300 harvesters. Each part balances on its own (its payments' wages by
	cost centre against those harvesters' credits)."""
	day = getdate(day)
	payments = _unposted(day)
	if not payments:
		return {"date": str(day), "skipped": "nothing to post"}
	plan, why = _plan_for(day)
	if not plan:
		return {"date": str(day), "skipped": why}

	harvesters = sorted({p.harvester_id for p in payments})
	parts = [harvesters[i:i + HARVESTERS_PER_JE] for i in range(0, len(harvesters), HARVESTERS_PER_JE)]
	made = []
	for n, part in enumerate(parts, 1):
		group = [p for p in payments if p.harvester_id in set(part)]
		made.append(_accrual_entry(day, plan, group, f"{n}/{len(parts)}"))
	return {"date": str(day), "journal_entries": made, "payments": len(payments)}


def _accrual_entry(day, plan, payments, part):
	by_cc = _split_by_cost_center(payments, plan)
	by_harvester = {}
	for p in payments:
		by_harvester[p.harvester_id] = by_harvester.get(p.harvester_id, 0) + flt(p.total_payment)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = plan.company
	je.posting_date = day
	je.cheque_no = f"COFFEE-PAY-{day}-{part.split('/')[0]}"
	je.cheque_date = day
	je.user_remark = (
		f"Coffee picker pay accrued for {day} (part {part}): {len(payments)} payments to {len(by_harvester)} harvesters, "
		f"{sum(p.total_buckets or 0 for p in payments)} buckets ({plan.name}). Paid out of the "
		f"{plan.payment_request} float by one Payment Entry per payment."
	)
	for cc, amount in by_cc.items():
		je.append("accounts", {"account": plan.expense_account, "cost_center": cc,
			"debit_in_account_currency": flt(amount, 2)})
	for harvester, amount in by_harvester.items():
		je.append("accounts", {"account": plan.payable_account, "party_type": "Harvester", "party": harvester,
			"credit_in_account_currency": flt(amount, 2)})
	je.insert()
	je.submit()
	for p in payments:
		frappe.db.set_value(p.doctype, p.name, "journal_entry", je.name, update_modified=False)
	return je.name


def pay_out(limit=BATCH):
	"""One Payment Entry per accrued payment that has none yet, oldest first."""
	made = 0
	for doctype in PAYMENT_DOCTYPES:
		for p in frappe.get_all(
			doctype,
			filters={"docstatus": 1, "journal_entry": ["is", "set"], "payment_entry": ["is", "not set"]},
			fields=["name", "harvester_id", "date", "total_payment", "journal_entry"],
			order_by="date asc, creation asc",
			limit_page_length=limit - made,
		):
			try:
				_payment_entry(doctype, p)
				frappe.db.commit()
				made += 1
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Coffee pay Payment Entry failed for {p.name}")
			if made >= limit:
				return made
	return made


def _payment_entry(doctype, p):
	plan, why = _plan_for(p.date)
	if not plan:
		frappe.throw(_("{0}: {1}").format(p.name, why))
	amount = flt(p.total_payment, 2)
	pe = frappe.new_doc("Payment Entry")
	pe.update({
		"payment_type": "Pay", "company": plan.company, "posting_date": p.date,
		"party_type": "Harvester", "party": p.harvester_id,
		"paid_from": plan.float_account, "paid_to": plan.payable_account,
		"paid_amount": amount, "received_amount": amount,
		"source_exchange_rate": 1, "target_exchange_rate": 1,
		"reference_no": p.name, "reference_date": p.date,
		"remarks": f"Coffee picker pay {p.name} ({doctype}) for {p.harvester_id}, out of the {plan.payment_request} float.",
	})
	# the harvester's credit on the day's accrual is what this payment settles
	pe.append("references", {"reference_doctype": "Journal Entry", "reference_name": p.journal_entry,
		"total_amount": amount, "outstanding_amount": amount, "allocated_amount": amount})
	pe.insert()
	pe.submit()
	frappe.db.set_value(doctype, p.name, "payment_entry", pe.name, update_modified=False)
	return pe.name


def pickers_payable_account(company):
	"""Coffee Pickers Payable for the company, created under Wages accruals
	(else the company's payables group) the first time it is needed."""
	name = frappe.db.get_value("Account", {"company": company, "account_name": PICKERS_PAYABLE, "is_group": 0})
	if name:
		return name
	parent = frappe.db.get_value("Account", {"company": company, "is_group": 1, "account_name": ["like", "%Wages accruals%"]}) \
		or frappe.db.get_value("Account", {"company": company, "is_group": 1, "account_name": "Accounts Payable"})
	acc = frappe.get_doc({"doctype": "Account", "company": company, "account_name": PICKERS_PAYABLE,
		"parent_account": parent, "account_type": "Payable", "root_type": "Liability",
		"account_currency": frappe.get_cached_value("Company", company, "default_currency")})
	acc.insert(ignore_permissions=True)
	return acc.name


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
