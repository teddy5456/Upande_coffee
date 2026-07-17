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

	active = frappe.db.get_value(
		"Coffee Season", {"is_active": 1}, ["target_cherry_kg", "season_name"], as_dict=True
	) or frappe._dict()

	return {
		"estimate": {
			"target_cherry_kg": flt(active.target_cherry_kg),
			"season_name": active.season_name,
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
		"clean": {"output_kg": clean_kg},
		"dispatch": {"total_weight_kg": dispatch_kg, "shipments": shipments},
		"invoices": {"total_billed": billed, "outstanding": outstanding},
	}


def _latest_moisture():
	"""Latest moisture reading per drying table."""
	rows = frappe.db.sql(
		"""SELECT r.drying_table, r.moisture_percentage, r.debes, r.batch, r.reading_date
		FROM `tabDaily Moisture Reading` r
		JOIN (SELECT drying_table, MAX(reading_date) md FROM `tabDaily Moisture Reading`
		      GROUP BY drying_table) x
		  ON x.drying_table = r.drying_table AND x.md = r.reading_date""",
		as_dict=True,
	)
	return {r.drying_table: r for r in rows}


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

	m_f = _between({}, "reading_date", from_date, to_date)
	moisture = frappe.get_all(
		"Daily Moisture Reading",
		filters=m_f,
		fields=["reading_date", "drying_table", "batch", "moisture_percentage", "debes", "read_by"],
		order_by="reading_date desc",
		limit_page_length=120,
	)
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
