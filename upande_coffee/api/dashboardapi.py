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


def clean_ratio():
	"""kg of cherry per kg of clean coffee (Coffee Settings, default 7):
	expected clean = cherry kg / ratio. An estimate, never accounting."""
	return flt(frappe.db.get_single_value("Coffee Settings", "cherry_to_clean_ratio")) or 7


def _grade_items():
	"""Clean-coffee grade items -> grade label. Live grades are the
	"Coffee Grade AA" items (Coffee-Grades item group); older sites used the
	bare codes of GRADE_ITEM_MAP."""
	from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import GRADE_ITEM_MAP

	items = set(frappe.get_all("Item", filters={"item_group": "Coffee-Grades"}, pluck="name"))
	items |= set(frappe.get_all("Item", filters={"name": ["in", list(GRADE_ITEM_MAP.values())]}, pluck="name"))
	return {i: i.replace("Coffee Grade ", "") for i in items}


def _vs_estimate(from_date, to_date, actual_debes, actual_kg):
	"""Harvest actual vs estimate, in debes and kg. The estimate is the
	expected debes of submitted Coffee Harvest Plans, each prorated to the
	days of the plan that fall in the range; kg converts it at the range's own
	harvested kg per debe."""
	f = {"docstatus": 1}
	if to_date:
		f["from_date"] = ["<=", to_date]
	if from_date:
		f["to_date"] = [">=", from_date]
	est_debes = 0.0
	plans = frappe.get_all("Coffee Harvest Plan", filters=f, fields=["name", "from_date", "to_date", "total_debes"])
	for p in plans:
		if not (p.from_date and p.to_date):
			continue
		start = max(getdate(p.from_date), getdate(from_date)) if from_date else getdate(p.from_date)
		end = min(getdate(p.to_date), getdate(to_date)) if to_date else getdate(p.to_date)
		days = (getdate(p.to_date) - getdate(p.from_date)).days + 1
		overlap = (end - start).days + 1
		if days > 0 and overlap > 0:
			est_debes += flt(p.total_debes) * overlap / days
	kg_per_debe = flt(actual_kg) / flt(actual_debes) if actual_debes else 0
	return {
		"estimate_debes": est_debes,
		"actual_debes": flt(actual_debes),
		"estimate_kg": est_debes * kg_per_debe,
		"actual_kg": flt(actual_kg),
		"kg_per_debe": kg_per_debe,
		"plans": len(plans),
	}


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
	ratio = clean_ratio()
	kg_by_day = dict(frappe.db.sql(
		"""SELECT date, SUM(total_weight_kg) FROM `tabHarvest Pickup`
		WHERE docstatus = 1 AND date >= %s {} GROUP BY date""".format("AND date <= %s" if to_date else ""),
		[start, to_date] if to_date else [start],
	))
	for r in daily:
		r.kg = flt(kg_by_day.get(r.date))
		r.clean_kg = r.kg / ratio

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
	received = _received(from_date, to_date)

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
			# buckets that actually arrived at the wet mill vs what was logged in the field
			"received_buckets": received.buckets,
			"received_weight_kg": received.weight,
			"short_buckets": received.short,
			# cherry / Coffee Settings.cherry_to_clean_ratio (Collins: "divide by 7")
			"estimated_clean_kg": total_weight / ratio,
			"clean_ratio": ratio,
		},
		"vs_estimate": _vs_estimate(from_date, to_date, total_buckets, total_weight),
		"drying": {
			"total_debes": total_debes,
			"by_type": [{"type": k, "debes": v} for k, v in sorted(by_type.items())],
			"readiness": readiness,
		},
		"clean": {
			"output_kg": clean_kg,
			# Forward-looking estimate from the season's forecast cherry, not a
			# measured figure -- see Coffee Settings.cherry_to_clean_ratio.
			"estimated_kg": target_cherry_kg / ratio if target_cherry_kg else 0,
		},
		"dispatch": {"total_weight_kg": dispatch_kg, "shipments": shipments},
		"invoices": {"total_billed": billed, "outstanding": outstanding},
	}


def _received(from_date, to_date):
	"""Buckets and kg received at the wet mill in the range (by pickup date).
	received_bucket_count is set when the receiver counted fewer than were
	loaded; older receipts had to match, so their total_buckets stands.
	A received pickup is a submitted one in workflow state "Received": on
	Kaitet the `received` check is never ticked (0 of 58 in October 2026), so
	filtering on it alone made the overview show nothing received."""
	state = " OR workflow_state = 'Received'" if frappe.db.has_column("Harvest Pickup", "workflow_state") else ""
	cond, params = [f"(received = 1{state})", "docstatus = 1"], {}
	if from_date:
		cond.append("date >= %(f)s")
		params["f"] = from_date
	if to_date:
		cond.append("date <= %(t)s")
		params["t"] = to_date
	counted = "COALESCE(NULLIF(received_bucket_count, 0), total_buckets)" if frappe.db.has_column(
		"Harvest Pickup", "received_bucket_count") else "total_buckets"
	r = frappe.db.sql(
		f"""SELECT COALESCE(SUM({counted}), 0) AS buckets,
			COALESCE(SUM(COALESCE(NULLIF(received_weight_kg, 0), total_weight_kg)), 0) AS weight,
			COALESCE(SUM(total_buckets - {counted}), 0) AS short
		FROM `tabHarvest Pickup` WHERE {" AND ".join(cond)}""",
		params, as_dict=True,
	)[0]
	return frappe._dict(buckets=flt(r.buckets), weight=flt(r.weight), short=flt(r.short))


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
	# Same full-range figure as the overview (_received), not the 50-row page
	# above: summing the page undercounted any range with more than 50 pickups.
	received = _received(from_date, to_date)
	pickup_count = frappe.db.count("Harvest Pickup", pk_f)

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
			"total_weight_kg": received.weight,
			"received_buckets": received.buckets,
			"total_payments": pay_total,
			"avg_rate": avg_rate,
			"harvest_days": agg.days or 0,
			"active_blocks": agg.blocks or 0,
			"harvesters": agg.harvesters or 0,
			"pickup_count": pickup_count,
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
	from upande_coffee.api.bookingapi import list_bookings

	from_date, to_date = _dates(from_date, to_date, season)

	def in_range(d):
		d = getdate(d) if d else None
		return d and (not from_date or d >= getdate(from_date)) and (not to_date or d <= getdate(to_date))

	# bookings are Coffee Sales Orders now (the Booking doctype is retired)
	bookings = [b for b in list_bookings(limit=1000) if in_range(b["booking_date"])
		and (not grower or b["grower"] == grower) and (not parchment_type or b["parchment_type"] == parchment_type)]

	# drafts are listed too (with their status) so a statement nobody has
	# submitted yet is visible; figures below count submitted milling only
	o_f = {"docstatus": ["<", 2]}
	if from_date:
		o_f["creation"] = [">=", str(getdate(from_date))]
	if to_date:
		o_f["creation"] = ["between", [str(getdate(from_date or "2000-01-01")), str(getdate(to_date)) + " 23:59:59"]]
	outturns = frappe.get_all(
		"Outturn Statement",
		filters=o_f,
		fields=["name", "outturn_number", "outturn_type", "grower", "parchment_weight",
				"output_weight", "milling_loss", "creation", "docstatus"],
		order_by="creation desc",
		limit_page_length=0,
	)
	drafts = [o for o in outturns if o.docstatus == 0]
	all_outturns, outturns = outturns, [o for o in outturns if o.docstatus == 1]
	by_type, by_grower = {}, {}
	for o in outturns:
		t = o.outturn_type or "—"
		by_type[t] = by_type.get(t, 0) + flt(o.output_weight)
		g = o.grower or "—"
		by_grower.setdefault(g, []).append(flt(o.milling_loss))

	# milled / dispatched / in store, by grade
	grades = _grade_items()
	by_grade = {}

	def g(item):
		return by_grade.setdefault(grades.get(item, item), {"grade": grades.get(item, item), "milled_kg": 0.0,
			"dispatched_kg": 0.0, "in_store_kg": 0.0})

	if outturns:
		for r in frappe.get_all("Outturn Details", filters={"parent": ["in", [o.name for o in outturns]],
				"parenttype": "Outturn Statement"}, fields=["item_code", "grade", "net_weight"]):
			g(r.item_code or r.grade)["milled_kg"] += flt(r.net_weight)
	if grades:
		dn_f = _between(_bu_filters("Delivery Note", {"docstatus": 1}), "posting_date", from_date, to_date)
		dns = frappe.get_all("Delivery Note", filters=dn_f, pluck="name")
		if dns:
			for r in frappe.get_all("Delivery Note Item", filters={"parent": ["in", dns], "item_code": ["in", list(grades)]},
					fields=["item_code", {"SUM": "stock_qty", "as": "qty"}], group_by="item_code"):
				g(r.item_code)["dispatched_kg"] += flt(r.qty)
		store = frappe.db.get_single_value("Coffee Settings", "milled_store_warehouse")
		if store:
			for b in frappe.get_all("Bin", filters={"warehouse": store, "item_code": ["in", list(grades)], "actual_qty": ["!=", 0]},
					fields=["item_code", "actual_qty"]):
				g(b.item_code)["in_store_kg"] += flt(b.actual_qty)
	stock = sorted(by_grade.values(), key=lambda r: -(r["milled_kg"] + r["in_store_kg"]))

	return {
		"kpis": {
			"total_bookings": len(bookings),
			"growers": len({b["grower"] for b in bookings if b["grower"]}),
			"parchment_kg": sum(flt(o.parchment_weight) for o in outturns),
			"output_kg": sum(flt(o.output_weight) for o in outturns),
			"avg_milling_loss": (
				sum(flt(o.milling_loss) for o in outturns) / len(outturns) if outturns else 0
			),
			"outturns": len(outturns),
			"draft_outturns": len(drafts),
			"dispatched_kg": sum(r["dispatched_kg"] for r in stock),
			"in_store_kg": sum(r["in_store_kg"] for r in stock),
		},
		"bookings": bookings,
		"outturns": all_outturns,
		"stock_by_grade": stock,
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

	# KPIs and the harvester breakdown must come from the FULL filtered set,
	# not the 200-row page above -- a season easily holds 700+ payments, and
	# deriving totals from the capped `payments` list silently undercounted
	# total_paid/harvesters_paid once a range passed that cap.
	paid_filters = dict(f, docstatus=1)
	agg = frappe.get_all(
		"Coffee Payment", filters=paid_filters,
		fields=[
			{"SUM": "total_payment", "as": "total"},
			{"COUNT": "name", "as": "cnt"},
			{"AVG": "rate", "as": "avgr"},
		],
	)[0]
	pending_count = frappe.db.count("Coffee Payment", dict(f, docstatus=0))
	by_harvester = frappe.get_all(
		"Coffee Payment", filters=paid_filters,
		group_by="harvester_id",
		fields=[
			"harvester_id",
			{"SUM": "total_payment", "as": "total_paid"},
			{"COUNT": "name", "as": "payments"},
			{"MAX": "date", "as": "last_date"},
		],
		order_by="total_paid desc",
		limit_page_length=0,
	)

	split = _backdated_split(from_date, to_date)
	for p in payments:
		p["backdated_buckets"] = split["by_payment"].get(p.name, 0)

	return {
		"kpis": {
			"total_paid": flt(agg.total) + split["form_paid"],
			"backdated_form_paid": split["form_paid"],
			"day_paid": split["day_paid"],
			"backdated_paid": split["backdated_paid"],
			"backdated_count": split["backdated_count"],
			"payment_count": int(agg.cnt or 0),
			"harvesters_paid": len(by_harvester),
			"pending_count": pending_count,
			"avg_rate": flt(agg.avgr),
		},
		"payments": payments,
		"by_harvester": by_harvester,
		"by_day": split["by_day"],
	}


def _backdated_split(from_date, to_date):
	"""Split money paid out into pay for buckets picked ON the pay day and
	backdated pay for earlier days.

	A picker can skip being paid on the 10th and collect on the 12th; the
	Coffee Payment is dated the 12th and covers both days' buckets. Counting
	it all as the 12th's pay overstates that day's labour cost. The day's own
	share is the buckets that harvester logged on the pay date (capped at what
	was paid); the remainder is backdated. Worked out from the Harvest Logs
	rather than harvest_log_names because almost no payment records those."""
	cond, params = ["cp.docstatus = 1"], {}
	if from_date:
		cond.append("cp.date >= %(f)s")
		params["f"] = from_date
	if to_date:
		cond.append("cp.date <= %(t)s")
		params["t"] = to_date
	log_cond = " AND ".join(c.replace("cp.", "") for c in cond[1:]) or "1=1"
	rows = frappe.db.sql(
		f"""SELECT cp.name, cp.date, cp.total_buckets, cp.rate, cp.total_payment,
			LEAST(cp.total_buckets, COALESCE(d.b, 0)) AS day_b
		FROM `tabCoffee Payment` cp
		LEFT JOIN (
			SELECT harvester_id, date, SUM(bucket_count) AS b FROM `tabHarvest Log`
			WHERE {log_cond} GROUP BY harvester_id, date
		) d ON d.harvester_id = cp.harvester_id AND d.date = cp.date
		WHERE {" AND ".join(cond)}""",
		params,
		as_dict=True,
	)
	# Coffee Backdated Payment: days a picker didn't collect, paid later from the desk form
	form_rows = frappe.db.sql(
		f"""SELECT cp.name, cp.date, cp.total_buckets, cp.rate, cp.total_payment, 0 AS day_b
		FROM `tabCoffee Backdated Payment` cp WHERE {" AND ".join(cond)}""",
		params, as_dict=True,
	) if frappe.db.table_exists("Coffee Backdated Payment") else []
	rows += form_rows
	by_payment, by_day = {}, {}
	day_paid = backdated_paid = 0.0
	for r in rows:
		day_amt = flt(r.day_b) * flt(r.rate)
		back_amt = flt(r.total_payment) - day_amt
		day_paid += day_amt
		back_buckets = int(r.total_buckets or 0) - int(r.day_b or 0)
		d = by_day.setdefault(str(r.date), {"date": r.date, "paid": 0.0, "day_paid": 0.0,
			"backdated_paid": 0.0, "backdated_payments": 0})
		d["paid"] += flt(r.total_payment)
		d["day_paid"] += day_amt
		if back_buckets > 0:
			by_payment[r.name] = back_buckets
			backdated_paid += back_amt
			d["backdated_paid"] += back_amt
			d["backdated_payments"] += 1
	return {
		"day_paid": day_paid,
		"backdated_paid": backdated_paid,
		"form_paid": sum(flt(r.total_payment) for r in form_rows),
		"backdated_count": len(by_payment),
		"by_payment": by_payment,
		"by_day": sorted(by_day.values(), key=lambda x: x["date"], reverse=True),
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

	grade_of = _grade_items()

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
		"ownership": _stock_by_owner(settings, list(pt_item.keys()) + list(grade_of.keys()), pt_item, grade_of),
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


def _stock_by_owner(settings, item_codes, pt_item, grade_of):
	"""Coffee at the dry mill and in the clean store, by owner.

	Worked out from the documents, so it holds whether or not parchment is
	batched: per outgrower order (non-internal customer), parchment received
	on submitted Coffee Intakes minus parchment milled on their Outturn
	Statements is still in the dry mill; clean coffee their outturns produced
	minus what has left on Delivery Notes (batches {outturn}-{grade}) is in the
	clean store. Whatever else sits in each place is the estate's own."""
	locations = {settings.dry_mill_warehouse: "Dry Mill", settings.milled_store_warehouse: "Clean Store"}
	locations.pop(None, None)
	if not (locations and item_codes):
		return {"rows": [], "totals": {}}
	on_hand = {}
	for b in frappe.get_all("Bin", filters={"item_code": ["in", item_codes], "warehouse": ["in", list(locations)],
			"actual_qty": [">", 0]}, fields=["warehouse", "actual_qty"]):
		loc = locations[b.warehouse]
		on_hand[loc] = on_hand.get(loc, 0) + flt(b.actual_qty)

	grades = [i for i in item_codes if i in grade_of]
	rows = []
	for so in frappe.db.sql(
		"""SELECT so.name, so.customer, so.custom_outturn_number AS outturn
		FROM `tabSales Order` so JOIN `tabCustomer` c ON c.name = so.customer
		WHERE so.docstatus = 1 AND c.is_internal_customer = 0 AND IFNULL(so.custom_outturn_number, '') != ''""",
		as_dict=True,
	):
		received = flt(frappe.db.sql(
			"""SELECT SUM(i.qty_kg) FROM `tabCoffee Intake Item` i JOIN `tabCoffee Intake` c ON c.name = i.parent
			WHERE c.sales_order = %s AND c.docstatus = 1""", so.name)[0][0])
		milled, clean = frappe.db.sql(
			"""SELECT COALESCE(SUM(parchment_weight), 0), COALESCE(SUM(output_weight), 0)
			FROM `tabOutturn Statement` WHERE custom_source_sales_order = %s AND docstatus = 1""", so.name)[0]
		dispatched = flt(frappe.db.sql(
			"""SELECT -SUM(sbe.qty) FROM `tabStock Ledger Entry` sle
			JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
			WHERE sle.item_code IN %(g)s AND sle.voucher_type = 'Delivery Note' AND sle.is_cancelled = 0
			  AND sbe.batch_no LIKE %(b)s""", {"g": tuple(grades) or ("",), "b": so.outturn + "-%"})[0][0]) if grades else 0
		for location, kg in (("Dry Mill", received - flt(milled)), ("Clean Store", flt(clean) - dispatched)):
			if kg > 0.001:
				rows.append({"owner": so.customer, "outgrower": True, "location": location,
					"order": so.name, "outturn": so.outturn, "kg": kg})
	totals = {}
	for loc in ("Dry Mill", "Clean Store"):
		theirs = sum(r["kg"] for r in rows if r["location"] == loc)
		ours = flt(on_hand.get(loc)) - theirs
		if theirs:
			totals["Outgrower - " + loc] = theirs
		if ours > 0.001:
			totals["Estate - " + loc] = ours
			rows.append({"owner": "Endebess Estate", "outgrower": False, "location": loc, "order": None, "outturn": None, "kg": ours})
	rows.sort(key=lambda x: (not x["outgrower"], x["owner"], x["location"]))
	return {"rows": rows, "totals": totals}
