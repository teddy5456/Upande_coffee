# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe

from upande_coffee.endebess_variants import og_item_code


def _resolve_grower_item(base_item_code, use_og):
	"""Return the OG variant when the DN is Endebess and the -OG item exists,
	otherwise the base grade item. Falls back silently if the -OG companion
	item hasn't been installed yet (e.g. on a site that skipped Phase 2)."""
	if not use_og or not base_item_code:
		return base_item_code
	og = og_item_code(base_item_code)
	if og and frappe.db.exists("Item", og):
		return og
	return base_item_code


def _is_endebess_grower_context(doctype, business_unit, customer):
	"""Endebess sale + a grower-coded customer → route to -OG variants."""
	if doctype not in ("Delivery Note", "Sales Invoice"):
		return False
	if not business_unit or "endebess" not in str(business_unit).lower():
		return False
	if not customer:
		return False
	return bool(frappe.db.get_value("Customer", customer, "custom_grower_code"))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_grower_customers(doctype, txt, searchfield, start, page_len, filters):
	"""Search only customers who have at least one Booking (i.e. are growers)."""
	return frappe.db.sql(
		"""
		SELECT DISTINCT c.name, c.customer_name
		FROM `tabCustomer` c
		JOIN `tabBooking` b ON b.grower = c.name
		WHERE (c.name LIKE %(txt)s OR c.customer_name LIKE %(txt)s)
		ORDER BY c.name
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"txt": f"%{txt}%", "page_len": int(page_len), "start": int(start)},
	)


@frappe.whitelist()
def get_outturn_items(
	outturn_name,
	doctype="Delivery Note",
	exclude_doc=None,
	business_unit=None,
	customer=None,
):
	"""All grade rows of a submitted outturn with remaining quantities —
	feeds the 'Get Items From → Outturn Statement' picker on DN and SI.

	When the caller passes an Endebess-grower context (business_unit contains
	'endebess' AND the customer has custom_grower_code set), each row's
	item_code is swapped to its `-OG` variant so downstream stock movements
	land on the zero-valuation companion item. Grade name and batch id stay
	on the base grade for reporting / lookups."""
	from upande_coffee.selling_hooks import get_dispatched_qty
	from frappe.utils import flt

	if doctype not in ("Delivery Note", "Sales Invoice"):
		frappe.throw(frappe._("Invalid doctype."))
	if not frappe.db.exists("Outturn Statement", outturn_name):
		return []
	doc = frappe.get_doc("Outturn Statement", outturn_name)
	if doc.docstatus != 1:
		frappe.throw(frappe._("Outturn {0} is not submitted.").format(outturn_name))
	bag_kg = frappe.db.get_single_value("Coffee Settings", "bag_weight_kg") or 60
	use_og = _is_endebess_grower_context(doctype, business_unit, customer)
	out = []
	for row in doc.table_cyvh:
		base_item_code = row.item_code or row.grade
		# Dispatched-qty ledger keys on the base grade item — one grade may be
		# dispatched as Internal AND Outgrower separately without double-counting.
		booked_base = get_dispatched_qty(doctype, outturn_name, base_item_code, exclude_doc=exclude_doc)
		og = og_item_code(base_item_code)
		booked_og = get_dispatched_qty(doctype, outturn_name, og, exclude_doc=exclude_doc) if og else 0
		booked = booked_base + booked_og
		remaining = max(flt(row.net_weight) - booked, 0)
		batch_id = f"{outturn_name}-{base_item_code}"
		out.append({
			"grade": row.grade,
			"item_code": _resolve_grower_item(base_item_code, use_og),
			"base_item_code": base_item_code,
			"is_outgrower": bool(use_og),
			"total_kg": flt(row.net_weight),
			"booked_kg": booked,
			"remaining_kg": flt(remaining, 2),
			"remaining_bags": int(remaining // bag_kg),
			"remaining_pockets": flt(remaining % bag_kg, 2),
			"bag_kg": bag_kg,
			"batch_id": batch_id if frappe.db.exists("Batch", batch_id) else None,
		})
	return out


@frappe.whitelist()
def get_outturn_grade(outturn_name, item_code, doctype="Delivery Note", exclude_doc=None):
	"""Grade totals and remaining quantity for one outturn + item, used to
	prefill bags/pockets on DN/SI rows and cap what the user may pick.

	`item_code` may be a base grade (AA) or its outgrower variant (AA-OG);
	the outturn stores base grades so we normalise before lookup, then sum
	dispatched qty across both the base and -OG so remaining accounts for
	Internal + Outgrower activity."""
	from upande_coffee.selling_hooks import get_dispatched_qty, get_grade_row
	from frappe.utils import flt

	if doctype not in ("Delivery Note", "Sales Invoice"):
		frappe.throw(frappe._("Invalid doctype."))
	suffix = frappe.db.get_single_value("Coffee Settings", "endebess_og_suffix") or "-OG"
	base_item_code = item_code
	if item_code and suffix and item_code.endswith(suffix):
		base_item_code = item_code[: -len(suffix)]
	grade = get_grade_row(outturn_name, base_item_code)
	if not grade:
		return {}
	bag_kg = frappe.db.get_single_value("Coffee Settings", "bag_weight_kg") or 60
	booked_base = get_dispatched_qty(doctype, outturn_name, base_item_code, exclude_doc=exclude_doc)
	og = og_item_code(base_item_code)
	booked_og = get_dispatched_qty(doctype, outturn_name, og, exclude_doc=exclude_doc) if og else 0
	booked = booked_base + booked_og
	remaining = max(flt(grade.net_weight) - booked, 0)
	batch_id = f"{outturn_name}-{base_item_code}"
	return {
		"grade": grade.grade,
		"total_kg": flt(grade.net_weight),
		"booked_kg": booked,
		"remaining_kg": remaining,
		"remaining_bags": int(remaining // bag_kg),
		"remaining_pockets": flt(remaining % bag_kg, 2),
		"bag_kg": bag_kg,
		"batch_id": batch_id if frappe.db.exists("Batch", batch_id) else None,
	}


@frappe.whitelist()
def get_available_outturn_grades(outturn_name):
	"""Return grade rows for a submitted Outturn Statement so the UI can pre-fill items."""
	if not outturn_name or not frappe.db.exists("Outturn Statement", outturn_name):
		return []
	doc = frappe.get_doc("Outturn Statement", outturn_name)
	if doc.docstatus != 1:
		return []
	result = []
	for row in doc.table_cyvh:
		batch_id = f"{outturn_name}-{row.grade}"
		result.append(
			{
				"grade": row.grade,
				"item_code": row.item_code or row.grade,
				"no_of_bags": row.no_of_bags or 0,
				"no_of_pockets": row.no_of_pockets or 0,
				"net_weight": row.net_weight or 0,
				"batch_id": batch_id if frappe.db.exists("Batch", batch_id) else None,
			}
		)
	return result
