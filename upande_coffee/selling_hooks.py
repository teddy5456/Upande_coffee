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
