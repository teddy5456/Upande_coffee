# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe


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
def get_outturn_items(outturn_name, doctype="Delivery Note", exclude_doc=None):
	"""All grade rows of a submitted outturn with remaining quantities —
	feeds the 'Get Items From → Outturn Statement' picker on DN and SI."""
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
	out = []
	for row in doc.table_cyvh:
		item_code = row.item_code or row.grade
		booked = get_dispatched_qty(doctype, outturn_name, item_code, exclude_doc=exclude_doc)
		remaining = max(flt(row.net_weight) - booked, 0)
		batch_id = f"{outturn_name}-{item_code}"
		out.append({
			"grade": row.grade,
			"item_code": item_code,
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
	prefill bags/pockets on DN/SI rows and cap what the user may pick."""
	from upande_coffee.selling_hooks import get_dispatched_qty, get_grade_row
	from frappe.utils import flt

	if doctype not in ("Delivery Note", "Sales Invoice"):
		frappe.throw(frappe._("Invalid doctype."))
	grade = get_grade_row(outturn_name, item_code)
	if not grade:
		return {}
	bag_kg = frappe.db.get_single_value("Coffee Settings", "bag_weight_kg") or 60
	booked = get_dispatched_qty(doctype, outturn_name, item_code, exclude_doc=exclude_doc)
	remaining = max(flt(grade.net_weight) - booked, 0)
	batch_id = f"{outturn_name}-{item_code}"
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
