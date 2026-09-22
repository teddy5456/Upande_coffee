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
def drying_tables():
	"""Drying tables with their current batch, for the moisture entry form."""
	return frappe.get_all(
		"Drying Table",
		fields=["name", "current_batch", "current_coffee_type"],
		order_by="name",
		limit_page_length=200,
	)


def _batch_stock_entry(batch_no):
	"""Latest Stock Entry that moved this batch — used as the required
	reference on the moisture Quality Inspection."""
	rows = frappe.db.sql(
		"""SELECT sle.voucher_no
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
		WHERE sle.voucher_type = 'Stock Entry' AND sle.is_cancelled = 0
		  AND (sle.batch_no = %(b)s OR sbe.batch_no = %(b)s)
		ORDER BY sle.posting_date DESC, sle.creation DESC LIMIT 1""",
		{"b": batch_no},
	)
	return rows[0][0] if rows else None


@frappe.whitelist(methods=["POST"])
def record_moisture(batch, moisture, drying_table=None, reading_date=None):
	"""Record a drying moisture reading as a native Quality Inspection
	(template 'Coffee Drying'). The batch's Stock Entry is auto-set as the
	required reference, so the user just enters batch + moisture."""
	moisture = flt(moisture)
	item = frappe.db.get_value("Batch", batch, "item")
	if not item:
		frappe.throw(_("Batch {0} not found.").format(batch))
	se = _batch_stock_entry(batch)
	if not se:
		frappe.throw(_("No Stock Entry found for batch {0} to reference.").format(batch))
	# pull the acceptance target from the template
	mx = frappe.db.get_value(
		"Item Quality Inspection Parameter",
		{"parent": "Coffee Drying", "specification": "Moisture %"},
		"max_value",
	)
	qi = frappe.get_doc({
		"doctype": "Quality Inspection",
		"inspection_type": "In Process",
		"report_date": reading_date or nowdate(),
		"reference_type": "Stock Entry",
		"reference_name": se,
		"item_code": item,
		"batch_no": batch,
		"sample_size": 1,
		# NOT manual_inspection: it suppresses the document-level status
		# roll-up, so an over-spec reading would sit inside an Accepted
		# inspection.
		"inspected_by": frappe.session.user,
		"custom_drying_table": drying_table,
		"quality_inspection_template": "Coffee Drying",
		"readings": [{
			"specification": "Moisture %", "numeric": 1,
			# reading_1 is what ERPNext grades against min/max
			# (QualityInspection.min_max_criteria_passed); reading_value alone
			# left every reading Rejected regardless of the number. Both are
			# written: reading_1 grades, reading_value is what the dashboard
			# queries read back.
			"reading_1": str(flt(moisture)),
			"reading_value": moisture,
			"min_value": 0, "max_value": mx or 12,
		}],
	})
	qi.insert()
	return {"name": qi.name, "status": qi.readings[0].status, "moisture": moisture}


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

	# parchment_item_for moved here when the Booking doctype was retired in
	# favour of Sales Order; the old import path raised ImportError, which broke
	# taking coffee off the drying tables entirely.
	from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import (
		parchment_item_for,
	)

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
