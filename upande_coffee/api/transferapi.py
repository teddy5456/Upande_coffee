# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Simple warehouse-to-warehouse transfers from the coffee web app —
# a plain Material Transfer Stock Entry on standard ERPNext.

import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def recent_transfers(limit=25):
	return frappe.get_all(
		"Stock Entry",
		filters={"stock_entry_type": ["in", ["Material Transfer", "Repack"]], "docstatus": 1},
		fields=["name", "stock_entry_type", "posting_date", "total_outgoing_value"],
		order_by="posting_date desc, creation desc",
		limit_page_length=int(limit),
	)


@frappe.whitelist(methods=["POST"])
def create_transfer(item_code, qty, from_warehouse, to_warehouse, batch_no=None):
	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero."))
	if from_warehouse == to_warehouse:
		frappe.throw(_("Source and target warehouse must differ."))
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.company = frappe.db.get_value("Warehouse", from_warehouse, "company")
	se.posting_date = nowdate()
	se.append("items", {
		"item_code": item_code, "qty": qty, "s_warehouse": from_warehouse,
		"t_warehouse": to_warehouse, "batch_no": batch_no, "use_serial_batch_fields": 1,
		"allow_zero_valuation_rate": 1,
	})
	se.insert()
	se.submit()
	return {"stock_entry": se.name}
