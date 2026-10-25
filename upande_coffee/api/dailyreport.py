# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# The evening email to management: the day's cherry harvested (buckets and
# kg), expected clean coffee (kg / Coffee Settings.cherry_to_clean_ratio) and
# the pickers paid -- with the season to date alongside. Sent to Coffee
# Settings.daily_report_recipients by the scheduler (hooks: 19:00).

import re

import frappe
from frappe.utils import flt, fmt_money, formatdate, getdate, nowdate

from upande_coffee.api.dashboardapi import clean_ratio


def _recipients():
	raw = frappe.db.get_single_value("Coffee Settings", "daily_report_recipients") or ""
	return [e for e in re.split(r"[\s,;]+", raw) if "@" in e]


def build(day=None):
	"""The day's figures and the season to date, as a dict."""
	day = getdate(day or nowdate())
	season_start = frappe.db.get_value("Coffee Season", {"is_active": 1}, "start_date") or day
	ratio = clean_ratio()

	def harvest(start, end):
		b = frappe.db.sql("""SELECT COALESCE(SUM(bucket_count), 0), COUNT(DISTINCT harvester_id)
			FROM `tabHarvest Log` WHERE date BETWEEN %s AND %s""", (start, end))[0]
		kg = frappe.db.sql("""SELECT COALESCE(SUM(total_weight_kg), 0) FROM `tabHarvest Pickup`
			WHERE docstatus = 1 AND date BETWEEN %s AND %s""", (start, end))[0][0]
		return {"buckets": flt(b[0]), "harvesters": int(b[1] or 0), "kg": flt(kg), "clean_kg": flt(kg) / ratio}

	def paid(start, end):
		out = {"count": 0, "amount": 0.0}
		for dt in ("Coffee Payment", "Coffee Backdated Payment"):
			if not frappe.db.table_exists(dt):
				continue
			r = frappe.db.sql(f"""SELECT COUNT(*), COALESCE(SUM(total_payment), 0) FROM `tab{dt}`
				WHERE docstatus = 1 AND date BETWEEN %s AND %s""", (start, end))[0]
			out["count"] += int(r[0] or 0)
			out["amount"] += flt(r[1])
			out[dt] = {"count": int(r[0] or 0), "amount": flt(r[1])}
		return out

	blocks = frappe.db.sql("""SELECT block, SUM(bucket_count) buckets FROM `tabHarvest Log`
		WHERE date = %s GROUP BY block ORDER BY buckets DESC""", day, as_dict=True)
	return {
		"day": day,
		"season_start": getdate(season_start),
		"ratio": ratio,
		"today": harvest(day, day),
		"season": harvest(season_start, day),
		"paid_today": paid(day, day),
		"paid_season": paid(season_start, day),
		"blocks": blocks,
	}


def render(d):
	def n(v, p=0):
		return f"{flt(v):,.{p}f}"

	def kes(v):
		return fmt_money(v, 0, "KES")

	t, s, pt, ps = d["today"], d["season"], d["paid_today"], d["paid_season"]
	cell = 'style="padding:6px 12px;border-bottom:1px solid #eee"'
	num = 'style="padding:6px 12px;border-bottom:1px solid #eee;text-align:right"'
	rows = [
		("Cherry harvested (buckets)", n(t["buckets"]), n(s["buckets"])),
		("Cherry harvested (kg)", n(t["kg"]), n(s["kg"])),
		(f"Expected clean coffee (kg, cherry ÷ {n(d['ratio'], 1).rstrip('0').rstrip('.')})", n(t["clean_kg"]), n(s["clean_kg"])),
		("Pickers who brought cherry", n(t["harvesters"]), n(s["harvesters"])),
		("Payments made", n(pt["count"]), n(ps["count"])),
		("Amount paid", kes(pt["amount"]), kes(ps["amount"])),
	]
	table = "".join(f"<tr><td {cell}>{a}</td><td {num}>{b}</td><td {num}>{c}</td></tr>" for a, b, c in rows)
	blocks = "".join(f"<tr><td {cell}>{frappe.utils.escape_html(b.block or '—')}</td><td {num}>{n(b.buckets)}</td></tr>"
		for b in d["blocks"]) or f'<tr><td {cell} colspan="2">No harvest logged today.</td></tr>'
	return f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
<p>Coffee harvest for <b>{d['day'].strftime('%A %d %B %Y')}</b>.</p>
<table style="border-collapse:collapse;min-width:420px">
<tr><th {cell}></th><th {num}>Today</th><th {num}>Season to date<br><small>from {formatdate(d['season_start'])}</small></th></tr>
{table}</table>
<p style="margin-top:18px"><b>Buckets by block today</b></p>
<table style="border-collapse:collapse;min-width:300px">{blocks}</table>
<p style="color:#888;font-size:12px;margin-top:18px">Kg is weighed cherry from submitted harvest pickups. Expected clean coffee
is an estimate. Payments include backdated payments.</p></div>"""


def send_daily_report(day=None):
	"""Scheduler entry point."""
	recipients = _recipients()
	if not recipients:
		return {"sent": False, "reason": "no recipients in Coffee Settings"}
	d = build(day)
	frappe.sendmail(
		recipients=recipients,
		subject=f"Coffee harvest {formatdate(d['day'])}: {flt(d['today']['buckets']):,.0f} buckets, "
			f"{flt(d['today']['kg']):,.0f} kg, {fmt_money(d['paid_today']['amount'], 0, 'KES')} paid",
		message=render(d),
		reference_doctype="Coffee Settings",
		reference_name="Coffee Settings",
	)
	return {"sent": True, "recipients": recipients}


@frappe.whitelist()
def send_now(day=None):
	frappe.only_for(["System Manager", "Coffee Harvest Manager"])
	return send_daily_report(day)
