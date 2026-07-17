# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Shared Delivery Note / Sales Invoice coffee logic, gated on the Business
# Unit name containing "endebess". Other business units are never touched.

import frappe
from frappe import _
from frappe.utils import flt


def is_coffee_document(doc):
	"""Business Unit comes from an Accounting Dimension (fieldname
	business_unit); older sites used a custom_business_unit field."""
	bu = doc.get("business_unit") or doc.get("custom_business_unit") or ""
	return "endebess" in bu.lower()


def get_grade_row(outturn, item_code):
	"""Find the grade row on an Outturn Statement matching an item.
	Outturn Details rows carry item_code (or fall back to the grade name)."""
	for row in frappe.get_all(
		"Outturn Details",
		filters={"parent": outturn, "parenttype": "Outturn Statement"},
		fields=["grade", "item_code", "no_of_bags", "no_of_pockets", "net_weight"],
	):
		if (row.item_code or row.grade) == item_code:
			return row
	return None


def get_dispatched_qty(doctype, outturn, item_code, exclude_doc=None):
	"""Total qty (kg) already booked against an outturn grade in submitted
	documents of this doctype."""
	item_table = f"{doctype} Item"
	rows = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(child.qty), 0)
		FROM `tab{item_table}` child
		JOIN `tab{doctype}` parent ON parent.name = child.parent
		WHERE parent.docstatus = 1
			AND child.custom_outturn_number = %(outturn)s
			AND child.item_code = %(item_code)s
			AND parent.name != %(exclude)s
		""",
		{"outturn": outturn, "item_code": item_code, "exclude": exclude_doc or ""},
	)
	return flt(rows[0][0]) if rows else 0


def calculate_item_weights(doc, method=None):
	"""Compute qty from bags + pockets (bags x bag weight + pockets kg), set
	batch from the row's outturn; on DN also force the milled-store source
	warehouse from Coffee Settings."""
	if not is_coffee_document(doc):
		return

	settings = frappe.get_cached_doc("Coffee Settings")
	bag_kg = settings.bag_weight_kg or 60
	is_dn = doc.doctype == "Delivery Note"

	if is_dn and settings.milled_store_warehouse:
		doc.set_warehouse = settings.milled_store_warehouse

	for row in doc.items:
		bags = flt(row.get("custom_no_of_bags"))
		pockets = flt(row.get("custom_no_of_pockets"))
		if bags or pockets:
			row.qty = bags * bag_kg + pockets

		if is_dn:
			if frappe.db.get_value("Item", row.item_code, "is_stock_item"):
				if settings.milled_store_warehouse:
					row.warehouse = settings.milled_store_warehouse
				row.uom = "Kilogram"
			else:
				row.warehouse = ""

		# batch is keyed by outturn + item
		outturn_ref = row.get("custom_outturn_number")
		if outturn_ref and row.item_code and not row.get("batch_no"):
			batch_id = f"{outturn_ref}-{row.item_code}"
			if frappe.db.exists("Batch", batch_id):
				row.batch_no = batch_id


def _is_endebess_bu(doc):
	"""Guard for Sales Order hook. Sales Order doesn't have the outturn/
	batch surface that is_coffee_document() was built for, and we want an
	unconditional zero-cost early exit for anything that isn't Endebess.
	Direct string check — no db lookup, no side effects."""
	bu = doc.get("business_unit") or doc.get("custom_business_unit") or ""
	return "endebess" in str(bu).lower()


def _endebess_config():
	"""Load the operator-configured item codes / price list from Coffee
	Settings. All fields are optional — the sync is a no-op when none of
	them are set, so ops can defer setup and edit SOs by hand."""
	s = frappe.get_cached_doc("Coffee Settings")
	return {
		"price_list": s.get("endebess_price_list"),
		"items": {
			"milling":   s.get("endebess_milling_item"),
			"handling":  s.get("endebess_handling_item"),
			"transport": s.get("endebess_transport_item"),
		},
		"bag_kg": s.get("bag_weight_kg") or 60,
	}


def _endebess_rate(item_code, price_list):
	if not item_code or not price_list:
		return None
	rate = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": price_list, "selling": 1},
		"price_list_rate",
	)
	return flt(rate) if rate is not None else None


def _resolve_endebess_rate(doc, item_code, fallback_price_list):
	rate = _endebess_rate(item_code, doc.get("selling_price_list"))
	if rate is not None:
		return rate
	return _endebess_rate(item_code, fallback_price_list) or 0


def _item_stock_uom(item_code, default="Nos"):
	if not item_code:
		return default
	return frappe.db.get_value("Item", item_code, "stock_uom") or default


def _assign_outturn_number(doc):
	"""Stamp a unique {WW}EM{#####} outturn number onto a Coffee SO on first
	save. WW is the ISO week (zero-padded to 2 digits), ##### resets to
	00001 at the start of each week and increments across ALL Coffee SOs
	filed that week. Example: 29EM00001, 29EM00002 … 30EM00001.

	Idempotent — no-op if the field already has a value."""
	if doc.get("custom_outturn_number"):
		return
	from datetime import datetime
	week = datetime.now().isocalendar()[1]
	prefix = f"{week:02d}EM"

	# Find the highest existing number in this week's series. LIKE on the
	# prefix + ORDER BY DESC picks the current max. Grabs FOR UPDATE so
	# two concurrent saves can't collide on the same next-number.
	last = frappe.db.sql(
		"""
		SELECT custom_outturn_number
		FROM `tabSales Order`
		WHERE custom_outturn_number LIKE %s
		ORDER BY custom_outturn_number DESC
		LIMIT 1
		FOR UPDATE
		""",
		(prefix + "%",),
	)
	next_num = 1
	if last and last[0][0]:
		try:
			next_num = int(last[0][0][len(prefix):]) + 1
		except (ValueError, IndexError):
			pass
	doc.custom_outturn_number = f"{prefix}{next_num:05d}"


def sync_endebess_service_items(doc, method=None):
	"""Sales Order before_validate hook.

	Bail path for non-Endebess SOs is the FIRST line — no field access, no
	db reads, no mutations. Vanilla Sales Orders pass through this in
	microseconds with zero observable effect.

	When the SO IS Endebess:
	  1. Stamp a Coffee outturn number if one isn't already assigned.
	  2. If the customer is internal (is_internal_customer=1), skip service
	     item auto-fill — internal Endebess doesn't get billed. Operator
	     manages the items table by hand (or leaves it empty; a placeholder
	     line may be needed to satisfy ERPNext's items-required check).
	  3. Otherwise: rebuild Milling / Handling / Transport service rows
	     from the tab fields. Everything else on the SO is preserved."""
	if not _is_endebess_bu(doc):
		return

	_assign_outturn_number(doc)

	cfg = _endebess_config()
	items = cfg["items"]
	owned = {code for code in items.values() if code}
	if not owned:
		return

	weight = flt(doc.get("custom_expected_parchment_weight_kg"))
	# Purge (a) our previously-added service rows so we can rebuild, and
	# (b) any blank rows — ERPNext's form auto-appends an empty row 1 that
	# would otherwise fail the mandatory-field check on save.
	doc.items = [
		r for r in doc.items
		if r.item_code and r.item_code not in owned
	]

	if weight <= 0:
		return

	bag_kg        = cfg["bag_kg"]
	milling_qty   = weight / 1000.0
	handling_qty  = weight / bag_kg
	transport_on  = bool(doc.get("custom_transport_expenses"))
	transport_qty = handling_qty if transport_on else 0.0

	specs = []
	if items["milling"]:
		specs.append((items["milling"], milling_qty))
	if items["handling"]:
		specs.append((items["handling"], handling_qty))
	if items["transport"] and transport_qty > 0:
		specs.append((items["transport"], transport_qty))

	for item_code, qty in specs:
		if not frappe.db.exists("Item", item_code):
			continue
		rate = _resolve_endebess_rate(doc, item_code, cfg["price_list"])
		uom = _item_stock_uom(item_code)
		doc.append("items", {
			"item_code":         item_code,
			"qty":               max(0.01, round(qty, 3)),
			"uom":               uom,
			"stock_uom":         uom,
			"rate":              rate,
			"price_list_rate":   rate,
			"conversion_factor": 1,
			"description":       item_code,
		})


def validate_outturn_limits(doc, method=None):
	"""Block dispatching/invoicing more of a grade than the outturn holds,
	counting rows in this document plus previously submitted documents."""
	if not is_coffee_document(doc):
		return

	totals = {}
	for row in doc.items:
		outturn = row.get("custom_outturn_number")
		if not outturn or not row.item_code:
			continue
		key = (outturn, row.item_code)
		totals[key] = totals.get(key, 0) + flt(row.qty)

	for (outturn, item_code), doc_qty in totals.items():
		grade = get_grade_row(outturn, item_code)
		if not grade:
			frappe.throw(
				_("Outturn {0} has no grade row for item {1}.").format(outturn, item_code)
			)
		booked = get_dispatched_qty(doc.doctype, outturn, item_code, exclude_doc=doc.name)
		available = flt(grade.net_weight) - booked
		if doc_qty > available + 0.01:
			frappe.throw(
				_(
					"Outturn {0}, grade {1}: this document books {2} kg but only {3} kg "
					"remain ({4} kg total, {5} kg already booked)."
				).format(
					outturn,
					grade.grade,
					frappe.bold(doc_qty),
					frappe.bold(flt(available, 2)),
					flt(grade.net_weight, 2),
					flt(booked, 2),
				)
			)
