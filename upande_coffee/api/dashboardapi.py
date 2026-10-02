# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Data endpoints for the /coffee-dashboard web page. These replace the
# site-level "API" Server Scripts that powered the old kaitet dashboard.

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

BU_LIKE = "%endebess%"


def _bu_filters(doctype, extra=None):
	"""Filters selecting Endebess-BU documents, tolerant of whether the
	Business Unit field comes from the Accounting Dimension (business_unit)
	or a legacy custom field (custom_business_unit)."""
	f = dict(extra or {})
	for col in ("business_unit", "custom_business_unit"):
		if frappe.db.has_column(doctype, col):
			f[col] = ["like", BU_LIKE]
			return f
	return f



def _agg(doctype, filters, func, field):
	rows = frappe.get_all(doctype, filters=filters, fields=[{func: field, "as": "v"}])
	return flt(rows[0].v) if rows and rows[0].v is not None else 0

def _dates(from_date=None, to_date=None, season=None):
	"""Resolve an effective date range from explicit dates and/or a season."""
	if season and frappe.db.exists("Coffee Season", season):
		s = frappe.db.get_value(
			"Coffee Season", season, ["start_date", "end_date"], as_dict=True
		)
		from_date = from_date or s.start_date
		to_date = to_date or s.end_date
	return from_date, to_date


def _between(filters, field, from_date, to_date):
	if from_date and to_date:
		filters[field] = ["between", [from_date, to_date]]
	elif from_date:
		filters[field] = [">=", from_date]
	elif to_date:
		filters[field] = ["<=", to_date]
	return filters


@frappe.whitelist()
def get_seasons():
	return {
		"seasons": frappe.get_all(
			"Coffee Season",
			fields=["name", "season_name", "is_active", "start_date", "end_date"],
			order_by="start_date desc",
		)
	}


@frappe.whitelist()
def get_overview(from_date=None, to_date=None, season=None):
	from upande_coffee.api.productionapi import resolve_season_target_kg
	from_date, to_date = _dates(from_date, to_date, season)

	# harvest
	hl = _between({}, "date", from_date, to_date)
	total_buckets = _agg("Harvest Log", hl, "SUM", "bucket_count")
	today_buckets = _agg("Harvest Log", {"date": nowdate()}, "SUM", "bucket_count")
	hp = _between({"docstatus": 1}, "date", from_date, to_date)
	total_weight = _agg("Harvest Pickup", hp, "SUM", "total_weight_kg")
	today_weight = _agg("Harvest Pickup", {"docstatus": 1, "date": nowdate()}, "SUM", "total_weight_kg")

	start = from_date or add_days(nowdate(), -30)
	daily = frappe.db.sql(
		"""SELECT date, SUM(bucket_count) AS count FROM `tabHarvest Log`
		WHERE date >= %s {} GROUP BY date ORDER BY date""".format(
			"AND date <= %s" if to_date else ""
		),
		[start, to_date] if to_date else [start],
		as_dict=True,
	)

	# drying snapshot
	tables = frappe.get_all(
		"Drying Table", fields=["name", "current_coffee_type", "current_debes", "current_batch"]
	)
	total_debes = sum(flt(t.current_debes) for t in tables)
	by_type = {}
	for t in tables:
		if t.current_coffee_type and flt(t.current_debes):
			by_type[t.current_coffee_type] = by_type.get(t.current_coffee_type, 0) + flt(t.current_debes)
	readiness = _readiness_counts()

	# clean output + dispatch + invoices
	ot = _between({"docstatus": 1}, "modified", from_date, to_date) if from_date or to_date else {"docstatus": 1}
	clean_kg = _agg("Outturn Statement", ot, "SUM", "output_weight")

	dn = _between(_bu_filters("Delivery Note", {"docstatus": 1}), "posting_date", from_date, to_date)
	dispatch_kg = _agg("Delivery Note", dn, "SUM", "total_qty")
	shipments = frappe.db.count("Delivery Note", dn)

	si = _between(_bu_filters("Sales Invoice", {"docstatus": 1}), "posting_date", from_date, to_date)
	billed = _agg("Sales Invoice", si, "SUM", "grand_total")
	outstanding = _agg("Sales Invoice", si, "SUM", "outstanding_amount")

	season_key = season if season and frappe.db.exists("Coffee Season", season) else frappe.db.get_value(
		"Coffee Season", {"is_active": 1}, "name"
	)
	active_name = frappe.db.get_value("Coffee Season", season_key, "season_name") if season_key else None
	target_cherry_kg = resolve_season_target_kg(season_key)
	yield_pct = flt(frappe.db.get_single_value("Coffee Settings", "expected_yield_pct")) or 20

	return {
		"estimate": {
			# Coffee Budget (top-down, by block) wins where one exists for this
			# season; Coffee Season.target_cherry_kg is the fallback for a
			# season nobody has budgeted yet. See productionapi.resolve_season_target_kg.
			"target_cherry_kg": target_cherry_kg,
			"season_name": active_name,
			"blocks": frappe.db.count("Warehouse", {"warehouse_type": "Block", "disabled": 0}),
		},
		"harvest": {
			"total_buckets": total_buckets,
			"total_weight_kg": total_weight,
			"today_buckets": today_buckets,
			"today_weight_kg": today_weight,
			"daily_buckets": daily,
		},
		"drying": {
			"total_debes": total_debes,
			"by_type": [{"type": k, "debes": v} for k, v in sorted(by_type.items())],
			"readiness": readiness,
		},
		"clean": {
			"output_kg": clean_kg,
			# Forward-looking estimate from the season's forecast cherry, not a
			# measured figure -- see Coffee Settings.expected_yield_pct.
			"estimated_kg": target_cherry_kg * yield_pct / 100 if target_cherry_kg else 0,
		},
		"dispatch": {"total_weight_kg": dispatch_kg, "shipments": shipments},
		"invoices": {"total_billed": billed, "outstanding": outstanding},
	}


def _latest_moisture():
	"""Latest moisture reading per drying table, from native Quality
	Inspections (template 'Coffee Drying', parameter 'Moisture %')."""
	rows = frappe.db.sql(
		"""SELECT qi.custom_drying_table AS drying_table,
		          r.reading_value AS moisture_percentage,
		          qi.batch_no AS batch, qi.report_date AS reading_date
		FROM `tabQuality Inspection` qi
		JOIN `tabQuality Inspection Reading` r
		  ON r.parent = qi.name AND r.specification = %(spec)s
		JOIN (SELECT custom_drying_table, MAX(report_date) md
		      FROM `tabQuality Inspection`
		      WHERE quality_inspection_template = %(tpl)s
		        AND IFNULL(custom_drying_table,'') != '' AND docstatus < 2
		      GROUP BY custom_drying_table) x
		  ON x.custom_drying_table = qi.custom_drying_table AND x.md = qi.report_date
		WHERE qi.quality_inspection_template = %(tpl)s AND qi.docstatus < 2""",
		{"spec": "Moisture %", "tpl": "Coffee Drying"},
		as_dict=True,
	)
	return {r.drying_table: r for r in rows}


def _moisture_readings(from_date=None, to_date=None, limit=120):
	"""Recent drying moisture readings from Quality Inspections."""
	conds = ["qi.quality_inspection_template = %(tpl)s", "qi.docstatus < 2", "r.specification = %(spec)s"]
	vals = {"tpl": "Coffee Drying", "spec": "Moisture %"}
	if from_date:
		conds.append("qi.report_date >= %(from)s"); vals["from"] = from_date
	if to_date:
		conds.append("qi.report_date <= %(to)s"); vals["to"] = to_date
	return frappe.db.sql(
		"""SELECT qi.report_date AS reading_date, qi.custom_drying_table AS drying_table,
		          qi.batch_no AS batch, r.reading_value AS moisture_percentage,
		          qi.inspected_by AS read_by
		FROM `tabQuality Inspection` qi
		JOIN `tabQuality Inspection Reading` r ON r.parent = qi.name
		WHERE {} ORDER BY qi.report_date DESC LIMIT {}""".format(" AND ".join(conds), int(limit)),
		vals, as_dict=True,
	)


def _readiness_counts():
	counts = {"ready": 0, "low": 0, "medium": 0, "high": 0}
	for r in _latest_moisture().values():
		p = flt(r.moisture_percentage)
		if p <= 14:
			counts["ready"] += 1
		elif p <= 24:
			counts["low"] += 1
		elif p <= 29:
			counts["medium"] += 1
		else:
			counts["high"] += 1
	return counts


@frappe.whitelist()
def get_harvest(days=30, from_date=None, to_date=None, season=None):
	from_date, to_date = _dates(from_date, to_date, season)
	if not from_date and flt(days):
		from_date = add_days(nowdate(), -int(flt(days)))

	log_f = _between({}, "date", from_date, to_date)
	logs = frappe.get_all(
		"Harvest Log",
		filters=log_f,
		fields=["date", "harvester_id", "block", "bucket_count", "picked_up", "paid"],
		order_by="date desc",
		limit_page_length=150,
	)
	agg = frappe.db.sql(
		"""SELECT COUNT(DISTINCT date) days, COUNT(DISTINCT harvester_id) harvesters,
		COUNT(DISTINCT block) blocks, SUM(bucket_count) buckets
		FROM `tabHarvest Log` {}""".format(_where("date", from_date, to_date)),
		_params(from_date, to_date),
		as_dict=True,
	)[0]

	pk_f = _between({"docstatus": ["<", 2]}, "date", from_date, to_date)
	pickups = frappe.get_all(
		"Harvest Pickup",
		filters=pk_f,
		fields=["name", "date", "total_buckets", "total_weight_kg", "workflow_state"],
		order_by="date desc",
		limit_page_length=50,
	)
	weight = sum(flt(p.total_weight_kg) for p in pickups if p.workflow_state == "Received")

	pay_f = _between({}, "date", from_date, to_date)
	payments = frappe.get_all(
		"Coffee Payment",
		filters=pay_f,
		fields=["date", "harvester_id", "total_buckets", "rate", "total_payment", "remark"],
		order_by="date desc",
		limit_page_length=150,
	)
	pay_total = _agg("Coffee Payment", pay_f, "SUM", "total_payment")
	avg_rate = _agg("Coffee Payment", pay_f, "AVG", "rate")

	by_harvester = frappe.db.sql(
		"""SELECT harvester_id, SUM(bucket_count) buckets FROM `tabHarvest Log` {}
		GROUP BY harvester_id ORDER BY buckets DESC LIMIT 15""".format(
			_where("date", from_date, to_date)
		),
		_params(from_date, to_date),
		as_dict=True,
	)
	by_block = frappe.db.sql(
		"""SELECT block, SUM(bucket_count) buckets FROM `tabHarvest Log` {}
		GROUP BY block ORDER BY buckets DESC""".format(_where("date", from_date, to_date)),
		_params(from_date, to_date),
		as_dict=True,
	)

	return {
		"kpis": {
			"total_buckets": agg.buckets or 0,
			"total_weight_kg": weight,
			"total_payments": pay_total,
			"avg_rate": avg_rate,
			"harvest_days": agg.days or 0,
			"active_blocks": agg.blocks or 0,
			"harvesters": agg.harvesters or 0,
			"pickup_count": len(pickups),
		},
		"logs": logs,
		"pickups": pickups,
		"payments": payments,
		"by_harvester": by_harvester,
		"by_block": by_block,
	}


def _where(field, from_date, to_date):
	conds = []
	if from_date:
		conds.append(f"`{field}` >= %s")
	if to_date:
		conds.append(f"`{field}` <= %s")
	return ("WHERE " + " AND ".join(conds)) if conds else ""


def _params(from_date, to_date):
	return [p for p in (from_date, to_date) if p]


@frappe.whitelist()
def get_drying(from_date=None, to_date=None, season=None):
	from_date, to_date = _dates(from_date, to_date, season)
	tables = frappe.get_all(
		"Drying Table",
		fields=["name", "status", "current_batch", "current_coffee_type", "current_debes", "date_loaded"],
	)
	latest = _latest_moisture()
	readiness = _readiness_counts()
	actives = [flt(r.moisture_percentage) for r in latest.values() if r.moisture_percentage is not None]

	moisture = _moisture_readings(from_date, to_date)
	a_f = _between({"docstatus": ["<", 2]}, "start_date", from_date, to_date)
	assignments = frappe.get_all(
		"Drying Assignment",
		filters=a_f,
		fields=["name", "batch", "start_date", "drying_status", "total_debes", "total_initial_weight_kg"],
		order_by="start_date desc",
		limit_page_length=100,
	)
	by_type = {}
	for t in tables:
		if t.current_coffee_type and flt(t.current_debes):
			by_type[t.current_coffee_type] = by_type.get(t.current_coffee_type, 0) + flt(t.current_debes)

	return {
		"kpis": {
			"total_tables": len(tables),
			"occupied": sum(1 for t in tables if t.current_batch),
			"available": sum(1 for t in tables if not t.current_batch),
			"ready": readiness["ready"],
			"high": readiness["high"],
			"avg_moisture": (sum(actives) / len(actives)) if actives else 0,
			"active_assignments": sum(1 for a in assignments if a.drying_status == "In Progress"),
		},
		"tables": tables,
		"latest_moisture": {k: v for k, v in latest.items()},
		"moisture": moisture,
		"assignments": assignments,
		"by_type": [{"type": k, "debes": v} for k, v in sorted(by_type.items())],
	}


@frappe.whitelist()
def get_milling(from_date=None, to_date=None, season=None, grower=None, parchment_type=None):
	from_date, to_date = _dates(from_date, to_date, season)
	b_f = _between({"docstatus": ["<", 2]}, "booking_date", from_date, to_date)
	if grower:
		b_f["grower"] = grower
	if parchment_type:
		b_f["parchment_type"] = parchment_type
	# Booking is retired; dashboards now show an empty bookings list when
	# the doctype isn't installed. Consumers should migrate to reading
	# Sales Order.custom_outturn_number for equivalent data.
	bookings = []
	if frappe.db.exists("DocType", "Booking"):
		bookings = frappe.get_all(
			"Booking",
			filters=b_f,
			fields=["name", "outturn_number", "grower", "grower_code", "parchment_type",
					"no_of_bags", "net_weight", "booking_date", "status"],
			order_by="booking_date desc",
			limit_page_length=150,
		)
	o_f = {"docstatus": 1}
	outturns = frappe.get_all(
		"Outturn Statement",
		filters=o_f,
		fields=["name", "outturn_number", "outturn_type", "grower", "parchment_weight",
				"output_weight", "milling_loss"],
		order_by="modified desc",
		limit_page_length=100,
	)
	by_type, by_grower = {}, {}
	for o in outturns:
		t = o.outturn_type or "—"
		by_type[t] = by_type.get(t, 0) + flt(o.output_weight)
		g = o.grower or "—"
		by_grower.setdefault(g, []).append(flt(o.milling_loss))

	return {
		"kpis": {
			"total_bookings": len(bookings),
			"growers": len({b.grower for b in bookings if b.grower}),
			"parchment_kg": sum(flt(o.parchment_weight) for o in outturns),
			"output_kg": sum(flt(o.output_weight) for o in outturns),
			"avg_milling_loss": (
				sum(flt(o.milling_loss) for o in outturns) / len(outturns) if outturns else 0
			),
			"outturns": len(outturns),
		},
		"bookings": bookings,
		"outturns": outturns,
		"by_type": [{"type": k, "output_kg": v} for k, v in sorted(by_type.items())],
		"by_grower": [
			{"grower": g, "avg_loss": sum(v) / len(v)} for g, v in sorted(by_grower.items()) if v
		],
	}


@frappe.whitelist()
def get_dispatch(from_date=None, to_date=None, season=None):
	from_date, to_date = _dates(from_date, to_date, season)
	f = _between(_bu_filters("Delivery Note", {"docstatus": 1}), "posting_date", from_date, to_date)
	dns = frappe.get_all(
		"Delivery Note",
		filters=f,
		fields=["name", "posting_date", "customer", "customer_name", "total_qty", "set_warehouse"],
		order_by="posting_date desc",
		limit_page_length=200,
	)
	by_customer, monthly = {}, {}
	for d in dns:
		c = d.customer_name or d.customer or "—"
		by_customer[c] = by_customer.get(c, 0) + flt(d.total_qty)
		m = str(d.posting_date)[:7]
		monthly[m] = monthly.get(m, 0) + flt(d.total_qty)

	return {
		"kpis": {
			"shipments": len(dns),
			"total_weight_kg": sum(flt(d.total_qty) for d in dns),
			"customers": len({d.customer for d in dns if d.customer}),
			"latest_date": str(dns[0].posting_date) if dns else None,
		},
		"dispatches": dns,
		"by_customer": [{"customer": k, "total_kg": v} for k, v in by_customer.items()],
		"monthly": [{"month": k, "total_kg": v} for k, v in sorted(monthly.items())],
	}


@frappe.whitelist()
def get_invoices(from_date=None, to_date=None, season=None):
	from_date, to_date = _dates(from_date, to_date, season)
	f = _between(_bu_filters("Sales Invoice", {"docstatus": 1}), "posting_date", from_date, to_date)
	invs = frappe.get_all(
		"Sales Invoice",
		filters=f,
		fields=["name", "customer", "customer_name", "posting_date", "due_date",
				"grand_total", "outstanding_amount", "status"],
		order_by="posting_date desc",
		limit_page_length=200,
	)
	billed = sum(flt(i.grand_total) for i in invs)
	outstanding = sum(flt(i.outstanding_amount) for i in invs)
	return {
		"kpis": {
			"count": len(invs),
			"customers": len({i.customer for i in invs if i.customer}),
			"total_billed": billed,
			"outstanding": outstanding,
			"paid_count": sum(1 for i in invs if flt(i.outstanding_amount) == 0),
			"collection_rate": ((billed - outstanding) / billed * 100) if billed else 0,
		},
		"invoices": invs,
	}


@frappe.whitelist()
def get_daily_planner():
	"""The weekly harvest-expectation forecast: what each block is expected to
	deliver, week by week, against what the weighbridge actually recorded.

	This is the same Coffee Forecast Override a picker sets from Kahawa Trail
	and a supervisor edits on the desktop /coffee-production grid — wrapped
	here via pickupapi.weekly_forecast_vs_actual() rather than re-derived, so
	the dashboard can never disagree with the grid or the mobile app about
	what a block is expected to deliver. The overdue-block alarm
	(productionapi.get_round_status) still rides alongside it as "Pick Now" —
	a live "what to do today" signal a weekly kg number doesn't replace.
	Both belong to the newer Coffee Production module, which may not be
	migrated on every site yet, so this returns availability rather than
	erroring where its doctypes don't exist.
	"""
	if not frappe.db.exists("DocType", "Coffee Block Profile"):
		return {"available": False, "weeks": [], "blocks": [], "alerts": []}

	from upande_coffee.api.pickupapi import weekly_forecast_vs_actual
	forecast = weekly_forecast_vs_actual(weeks_back=2, weeks_ahead=4)
	weeks = forecast["weeks"]

	today = str(getdate(nowdate()))
	this_week_index = next((i for i, w in enumerate(weeks) if w["start"] <= today <= w["end"]), None)

	expected_total = actual_total = 0.0
	if this_week_index is not None:
		for b in forecast["blocks"]:
			expected_total += flt((b["expected"] or [])[this_week_index])
			actual_total += flt((b["actual"] or [])[this_week_index])

	alerts = []
	try:
		from upande_coffee.api.productionapi import get_round_status
		alerts = [r for r in get_round_status() if r.get("overdue")]
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_daily_planner: get_round_status")

	return {
		"available": True,
		"weeks": weeks,
		"blocks": forecast["blocks"],
		"this_week_index": this_week_index,
		"kpis": {
			"this_week_expected_kg": round(expected_total, 1),
			"this_week_actual_kg": round(actual_total, 1),
			"variance_pct": round((actual_total - expected_total) / expected_total * 100, 1) if expected_total else None,
			"overdue_blocks": len(alerts),
		},
		"alerts": alerts,
	}


@frappe.whitelist()
def get_payments(from_date=None, to_date=None, season=None):
	from_date, to_date = _dates(from_date, to_date, season)
	f = _between({}, "date", from_date, to_date)
	payments = frappe.get_all(
		"Coffee Payment",
		filters=f,
		fields=["name", "date", "harvester_id", "total_buckets", "rate", "total_payment", "docstatus", "paid"],
		order_by="date desc",
		limit_page_length=200,
	)
	paid_total = sum(flt(p.total_payment) for p in payments if p.docstatus == 1)
	pending = [p for p in payments if p.docstatus == 0]

	by_harvester = {}
	for p in payments:
		if p.docstatus != 1:
			continue
		row = by_harvester.setdefault(
			p.harvester_id, {"harvester_id": p.harvester_id, "total_paid": 0, "payments": 0, "last_date": None}
		)
		row["total_paid"] += flt(p.total_payment)
		row["payments"] += 1
		if not row["last_date"] or p.date > row["last_date"]:
			row["last_date"] = p.date

	return {
		"kpis": {
			"total_paid": paid_total,
			"payment_count": sum(1 for p in payments if p.docstatus == 1),
			"harvesters_paid": len(by_harvester),
			"pending_count": len(pending),
			"avg_rate": _agg("Coffee Payment", dict(f, docstatus=1), "AVG", "rate"),
		},
		"payments": payments,
		"by_harvester": sorted(by_harvester.values(), key=lambda r: -r["total_paid"]),
	}


@frappe.whitelist()
def get_stock():
	"""Live coffee stock for the dashboard Stock section:
	- parchment on hand, split by parchment type (across all Endebess coffee
	  warehouses) and by warehouse;
	- clean coffee on hand, split by grade (in the milled store)."""
	settings = frappe.get_cached_doc("Coffee Settings")

	# parchment-type items → the stock items each type is stored as
	ptypes = frappe.get_all("Parchment Type", fields=["name", "item"])
	pt_item = {}
	for p in ptypes:
		item = p.item or (p.name if frappe.db.exists("Item", p.name) else None)
		if item:
			pt_item[item] = p.name
	if settings.parchment_item:
		pt_item.setdefault(settings.parchment_item, "Parchment")

	# grade items
	from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import (
		GRADE_ITEM_MAP,
	)
	grade_of = {}
	for grade, item in GRADE_ITEM_MAP.items():
		grade_of[item] = grade

	# balance per (item, warehouse) from the Bin table (fast, live)
	def _balances(item_codes):
		if not item_codes:
			return []
		return frappe.get_all(
			"Bin",
			filters={"item_code": ["in", list(item_codes)], "actual_qty": [">", 0]},
			fields=["item_code", "warehouse", "actual_qty"],
			limit_page_length=0,
		)

	# ── parchment ──────────────────────────────────────────────────────────
	p_bal = _balances(pt_item.keys())
	parchment_total = 0
	by_type, by_wh = {}, {}
	for b in p_bal:
		q = flt(b.actual_qty)
		parchment_total += q
		t = pt_item.get(b.item_code, b.item_code)
		by_type[t] = by_type.get(t, 0) + q
		by_wh[b.warehouse] = by_wh.get(b.warehouse, 0) + q

	# ── clean coffee ───────────────────────────────────────────────────────
	c_bal = _balances(grade_of.keys())
	clean_total = 0
	by_grade, clean_by_wh = {}, {}
	for b in c_bal:
		q = flt(b.actual_qty)
		clean_total += q
		g = grade_of.get(b.item_code, b.item_code)
		by_grade[g] = by_grade.get(g, 0) + q
		clean_by_wh[b.warehouse] = clean_by_wh.get(b.warehouse, 0) + q

	return {
		"parchment": {
			"total_kg": parchment_total,
			"by_type": [{"type": k, "kg": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
			"by_warehouse": [{"warehouse": k, "kg": v} for k, v in sorted(by_wh.items(), key=lambda x: -x[1])],
		},
		"clean": {
			"total_kg": clean_total,
			"by_grade": [{"grade": k, "kg": v} for k, v in sorted(by_grade.items(), key=lambda x: -x[1])],
			"by_warehouse": [{"warehouse": k, "kg": v} for k, v in sorted(clean_by_wh.items(), key=lambda x: -x[1])],
		},
	}
