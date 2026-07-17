# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Milling operations for the coffee web app: record the outturn (grades and
# amounts that came out of the mill). Submitting the Outturn Statement fires
# its repack: parchment consumed at the dry mill -> grade items in the
# milled store, batched "{outturn}-{grade}".

import json

import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_defaults():
	from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import GRADE_ITEM_MAP

	settings = frappe.get_cached_doc("Coffee Settings")
	# bookings at the mill without a submitted outturn statement yet
	used = set(
		frappe.get_all("Outturn Statement", filters={"docstatus": 1}, pluck="outturn_number")
	)
	bookings = [
		b for b in frappe.get_all(
			"Booking",
			filters={"docstatus": 1, "status": "Transferred"},
			fields=["name", "grower", "parchment_type", "net_weight", "booking_date"],
			order_by="booking_date asc",
		)
		if b.name not in used
	]
	return {
		"bookings": bookings,
		"grades": [g for g in GRADE_ITEM_MAP if frappe.db.exists("Item", GRADE_ITEM_MAP[g])],
		"all_grades": list(GRADE_ITEM_MAP),
		"bag_kg": settings.bag_weight_kg or 60,
		"dry_mill": settings.dry_mill_warehouse,
		"milled_store": settings.milled_store_warehouse,
	}


@frappe.whitelist(methods=["POST"])
def record_outturn(outturn_number, grades, comments=None):
	"""Create and submit an Outturn Statement for a milled booking.

	grades: JSON list of {grade, no_of_bags, no_of_pockets}. Weights, output
	total and milling loss compute server-side; submit fires the repack."""
	grades = json.loads(grades) if isinstance(grades, str) else grades
	rows = [g for g in grades if flt(g.get("no_of_bags")) or flt(g.get("no_of_pockets"))]
	if not rows:
		frappe.throw(_("Enter at least one grade with bags or pockets."))

	bag_kg = frappe.get_cached_doc("Coffee Settings").bag_weight_kg or 60
	doc = frappe.get_doc({
		"doctype": "Outturn Statement",
		"outturn_type": "Normal",
		"outturn_number": outturn_number,
		"comments": comments,
		"table_cyvh": [
			{
				"grade": g["grade"],
				"no_of_bags": int(flt(g.get("no_of_bags"))),
				"no_of_pockets": flt(g.get("no_of_pockets")),
			}
			for g in rows
		],
	})
	doc.output_weight = sum(
		int(flt(g.get("no_of_bags"))) * bag_kg + flt(g.get("no_of_pockets")) for g in rows
	)
	doc.insert()
	doc.submit()
	doc.reload()
	return {
		"name": doc.name,
		"output_weight": doc.output_weight,
		"parchment_weight": doc.parchment_weight,
		"milling_loss": doc.milling_loss,
	}
