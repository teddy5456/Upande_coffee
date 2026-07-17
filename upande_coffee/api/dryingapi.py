# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Drying operations for the coffee web app.
#
# remove_from_drying shows the doctype-reduction pattern: taking coffee off
# the drying tables into a store bin is just a Repack Stock Entry on standard
# ERPNext — no custom removal doctypes needed.

import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def get_defaults():
	return {
		"parchment_types": frappe.get_all("Parchment Type", fields=["name", "item"], order_by="name"),
		"items": frappe.get_all("Item", filters={"is_stock_item": 1, "disabled": 0},
			fields=["name", "item_name"], order_by="name", limit_page_length=200),
		"bins": frappe.get_all("Warehouse",
			filters={"is_group": 0, "disabled": 0, "custom_farm": ["like", "%endebess%"],
					"warehouse_type": ["not in", ["Greenhouse", "Block", "Transit"]]},
			fields=["name"], order_by="name", limit_page_length=200),
		"wet_mill": frappe.db.get_single_value("Coffee Settings", "wet_mill_warehouse"),
	}


@frappe.whitelist()
def batch_stock(warehouse):
	"""Batch-wise stock in a warehouse, for picking what to remove.

	Handles both storage models: legacy batch_no on the ledger entry and the
	v16 Serial and Batch Bundle."""
	return frappe.db.sql(
		"""SELECT sle.item_code,
			COALESCE(sbe.batch_no, sle.batch_no) AS batch_no,
			SUM(COALESCE(sbe.qty, sle.actual_qty)) AS qty
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = sle.serial_and_batch_bundle
		WHERE sle.warehouse = %s AND sle.is_cancelled = 0
		GROUP BY sle.item_code, COALESCE(sbe.batch_no, sle.batch_no)
		HAVING batch_no IS NOT NULL AND batch_no != '' AND qty > 0
		ORDER BY sle.item_code, batch_no""",
		warehouse,
		as_dict=True,
	)


@frappe.whitelist(methods=["POST"])
def remove_from_drying(item_code, batch_no, qty, from_warehouse, outputs, to_warehouse=None):
	"""Take coffee off the drying tables.

	Drying loses weight (1000 kg wet in → 300 kg dry out) and one batch can
	come off as SEVERAL grades, so this is a Repack: `qty` is the WET weight
	consumed, `outputs` is a JSON list of {item_code, qty} — what actually
	came out, weighed dry, per grade."""
	import json

	from upande_coffee.upande_coffee.doctype.booking.booking import parchment_item_for

	outputs = json.loads(outputs) if isinstance(outputs, str) else outputs
	for o in outputs:
		# rows may name a parchment type instead of a raw item
		if o.get("parchment_type") and not o.get("item_code"):
			o["item_code"] = parchment_item_for(o["parchment_type"])
	outputs = [o for o in outputs if o.get("item_code") and flt(o.get("qty")) > 0]
	qty = flt(qty)
	total_out = sum(flt(o["qty"]) for o in outputs)
	if qty <= 0 or not outputs:
		frappe.throw(_("Enter the wet weight consumed and at least one output grade."))
	if total_out > qty:
		frappe.throw(_("Dry weight out ({0} kg) cannot exceed wet weight in ({1} kg).").format(total_out, qty))
	for o in outputs:
		o["to_warehouse"] = o.get("to_warehouse") or to_warehouse
		if not o["to_warehouse"]:
			frappe.throw(_("Pick a destination bin for {0}.").format(o["item_code"]))

	# uniform per-kg rate preserving consumed value (multi-output repack rule)
	src_rate = flt(frappe.db.get_value(
		"Bin", {"warehouse": from_warehouse, "item_code": item_code}, "valuation_rate"))
	out_rate = (src_rate * qty / total_out) if total_out else 0

	se = frappe.new_doc("Stock Entry")
	se.company = frappe.db.get_value("Warehouse", from_warehouse, "company")
	se.posting_date = nowdate()
	se.stock_entry_type = "Repack"
	se.remarks = f"Off drying tables: {qty} kg wet -> {total_out} kg dry ({len(outputs)} grades)"
	se.append("items", {"item_code": item_code, "qty": qty, "s_warehouse": from_warehouse,
						"batch_no": batch_no, "use_serial_batch_fields": 1,
						"allow_zero_valuation_rate": 1})
	for o in outputs:
		out_item = o["item_code"]
		target_batch = None
		if frappe.db.get_value("Item", out_item, "has_batch_no"):
			target_batch = f"{o.get('parchment_type') or out_item}-{nowdate()}"
			if not frappe.db.exists("Batch", target_batch):
				frappe.get_doc({"doctype": "Batch", "batch_id": target_batch,
								"item": out_item}).insert(ignore_permissions=True)
		se.append("items", {"item_code": out_item, "qty": flt(o["qty"]), "t_warehouse": o["to_warehouse"],
							"batch_no": target_batch, "use_serial_batch_fields": 1,
							"is_finished_item": 1, "set_basic_rate_manually": 1,
							"basic_rate": out_rate, "allow_zero_valuation_rate": 1})

	se.insert()
	se.submit()
	return {"stock_entry": se.name, "type": se.stock_entry_type,
			"out_kg": flt(total_out, 2), "loss_kg": flt(qty - total_out, 2)}
