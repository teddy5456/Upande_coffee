# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# A picker who skips collecting on the 10th and is paid on the 12th is paid for
# both days, so the 12th pays out more than the 12th harvested. A payment is
# backdated when it pays more buckets than that harvester logged on the pay date.

import json

import frappe
from frappe.utils import add_days, date_diff, flt, formatdate, nowdate


def execute(filters=None):
	f = filters or {}
	params = {"f": f.get("from_date") or add_days(nowdate(), -30), "t": f.get("to_date") or nowdate()}
	extra = ""
	if f.get("harvester"):
		extra, params["h"] = " AND cp.harvester_id = %(h)s", f.get("harvester")

	rows = frappe.db.sql(
		f"""SELECT cp.name, cp.date, cp.harvester_id, cp.total_buckets, cp.rate, cp.total_payment,
			COALESCE(d.b, 0) AS day_b, cp.harvest_log_names
		FROM `tabCoffee Payment` cp
		LEFT JOIN (
			SELECT harvester_id, date, SUM(bucket_count) AS b FROM `tabHarvest Log`
			WHERE date BETWEEN %(f)s AND %(t)s GROUP BY harvester_id, date
		) d ON d.harvester_id = cp.harvester_id AND d.date = cp.date
		WHERE cp.docstatus = 1 AND cp.date BETWEEN %(f)s AND %(t)s{extra}
		ORDER BY cp.date DESC, cp.harvester_id""",
		params,
		as_dict=True,
	)
	for r in rows:
		r.day_paid_b = min(r.total_buckets or 0, r.day_b)
		r.back_b = (r.total_buckets or 0) - r.day_paid_b
		r.back_amt = flt(r.back_b) * flt(r.rate)

	total_paid = sum(flt(r.total_payment) for r in rows)
	backdated = [r for r in rows if r.back_b > 0]
	back_paid = sum(r.back_amt for r in backdated)
	summary = [
		{"label": "Paid out", "value": total_paid, "datatype": "Currency", "indicator": "Blue"},
		{"label": "For buckets picked on the pay day", "value": total_paid - back_paid, "datatype": "Currency", "indicator": "Green"},
		{"label": "Backdated", "value": back_paid, "datatype": "Currency", "indicator": "Orange"},
		{"label": "Backdated payments", "value": len(backdated), "datatype": "Int", "indicator": "Orange"},
	]
	if f.get("view") == "By Pay Day":
		columns, result = _by_day(rows, params)
	else:
		columns, result = _backdated(backdated)
	return columns, result, None, None, summary


def _by_day(rows, params):
	harvested = dict(frappe.db.sql(
		"""SELECT date, SUM(bucket_count) FROM `tabHarvest Log`
		WHERE date BETWEEN %(f)s AND %(t)s GROUP BY date""",
		params,
	))
	days = {}
	for r in rows:
		d = days.setdefault(r.date, {"date": r.date, "harvested": flt(harvested.get(r.date)), "paid_b": 0,
			"day_b": 0, "back_b": 0, "paid": 0, "back_amt": 0, "back_n": 0})
		d["paid_b"] += r.total_buckets or 0
		d["day_b"] += r.day_paid_b
		d["back_b"] += r.back_b
		d["paid"] += flt(r.total_payment)
		d["back_amt"] += r.back_amt
		d["back_n"] += 1 if r.back_b > 0 else 0
	for d in days.values():
		d["over"] = max(0, d["paid_b"] - d["harvested"])
	columns = [
		{"fieldname": "date", "label": "Pay Day", "fieldtype": "Date", "width": 110},
		{"fieldname": "harvested", "label": "Buckets Harvested", "fieldtype": "Float", "precision": 0, "width": 140},
		{"fieldname": "paid_b", "label": "Buckets Paid", "fieldtype": "Float", "precision": 0, "width": 120},
		{"fieldname": "over", "label": "Paid More Than Harvested", "fieldtype": "Float", "precision": 0, "width": 190},
		{"fieldname": "day_b", "label": "Paid for That Day", "fieldtype": "Float", "precision": 0, "width": 140},
		{"fieldname": "back_b", "label": "Backdated Buckets", "fieldtype": "Float", "precision": 0, "width": 140},
		{"fieldname": "back_n", "label": "Backdated Payments", "fieldtype": "Int", "width": 150},
		{"fieldname": "paid", "label": "Paid Out", "fieldtype": "Currency", "width": 120},
		{"fieldname": "back_amt", "label": "Backdated Amount", "fieldtype": "Currency", "width": 140},
	]
	return columns, list(days.values())


def _backdated(backdated):
	for r in backdated:
		r.logs = json.loads(r.harvest_log_names) if r.harvest_log_names else []
	names = [n for r in backdated for n in r.logs]
	log_date = dict(frappe.get_all("Harvest Log", filters={"name": ["in", names]}, fields=["name", "date"],
		as_list=True)) if names else {}
	result = []
	for r in backdated:
		# Only payments made since 2026-10-14 record which logs they covered.
		earlier = sorted({log_date[n] for n in r.logs if n in log_date and log_date[n] < r.date})
		result.append({
			"date": r.date, "payment": r.name, "harvester": r.harvester_id,
			"total_buckets": r.total_buckets, "day_b": r.day_paid_b, "back_b": r.back_b, "rate": r.rate,
			"total_payment": r.total_payment, "back_amt": r.back_amt,
			"harvested_on": ", ".join(formatdate(x, "dd MMM") for x in earlier) if earlier else "not recorded",
			"days_late": date_diff(r.date, earlier[0]) if earlier else None,
		})
	columns = [
		{"fieldname": "date", "label": "Paid On", "fieldtype": "Date", "width": 105},
		{"fieldname": "payment", "label": "Coffee Payment", "fieldtype": "Link", "options": "Coffee Payment", "width": 130},
		{"fieldname": "harvester", "label": "Harvester", "fieldtype": "Link", "options": "Harvester", "width": 140},
		{"fieldname": "total_buckets", "label": "Buckets Paid", "fieldtype": "Int", "width": 110},
		{"fieldname": "day_b", "label": "Picked on Pay Day", "fieldtype": "Int", "width": 140},
		{"fieldname": "back_b", "label": "Backdated Buckets", "fieldtype": "Int", "width": 140},
		{"fieldname": "harvested_on", "label": "Backdated Buckets Picked On", "fieldtype": "Data", "width": 200},
		{"fieldname": "days_late", "label": "Days Late", "fieldtype": "Int", "width": 90},
		{"fieldname": "rate", "label": "Rate", "fieldtype": "Currency", "width": 80},
		{"fieldname": "total_payment", "label": "Paid", "fieldtype": "Currency", "width": 100},
		{"fieldname": "back_amt", "label": "Backdated Amount", "fieldtype": "Currency", "width": 140},
	]
	return columns, result
