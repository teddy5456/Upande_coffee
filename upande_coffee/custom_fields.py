"""Create and maintain Upande Coffee custom fields on standard ERPNext doctypes.

Coffee visibility is gated on the Business Unit name containing "endebess"
(fieldname business_unit, created by the Business Unit Accounting Dimension). No
delivery-type checkbox — other business units never see these fields.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

_GATE_HDR = "eval:((doc.business_unit||doc.custom_business_unit||'')+'').toLowerCase().includes('endebess')"
_GATE_ITEM = "eval:((parent.business_unit||parent.custom_business_unit||'')+'').toLowerCase().includes('endebess')"


def create_coffee_custom_fields():
	"""Idempotently create Upande Coffee custom fields.
	Called on after_install and after_migrate.
	"""
	create_custom_fields(
		{
			# ── Delivery Note Item (row-level: same grade may repeat across
			#    rows, each row tied to its own outturn) ───────────────────────
			"Delivery Note Item": [
				{
					"fieldname": "custom_coffee_sec",
					"fieldtype": "Section Break",
					"label": "Coffee",
					"insert_after": "batch_no",
					"depends_on": _GATE_ITEM,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_outturn_number",
					"fieldtype": "Link",
					"label": "Outturn Statement",
					"options": "Outturn Statement",
					"insert_after": "custom_coffee_sec",
					"depends_on": _GATE_ITEM,
					"in_list_view": 0,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_no_of_bags",
					"fieldtype": "Int",
					"label": "No of Bags",
					"insert_after": "custom_outturn_number",
					"depends_on": _GATE_ITEM,
					"non_negative": 1,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_no_of_pockets",
					"fieldtype": "Float",
					"label": "Pockets (kg)",
					"insert_after": "custom_no_of_bags",
					"depends_on": _GATE_ITEM,
					"non_negative": 1,
					"module": "Upande Coffee",
				},
			],
			# ── Sales Invoice Item — same eval, outturn per row, bags+pockets
			#    drive qty exactly like the DN ──────────────────────────────────
			"Sales Invoice Item": [
				{
					"fieldname": "custom_coffee_sec",
					"fieldtype": "Section Break",
					"label": "Coffee",
					"insert_after": "batch_no",
					"depends_on": _GATE_ITEM,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_outturn_number",
					"fieldtype": "Link",
					"label": "Outturn Statement",
					"options": "Outturn Statement",
					"insert_after": "custom_coffee_sec",
					"depends_on": _GATE_ITEM,
					"in_list_view": 0,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_no_of_bags",
					"fieldtype": "Int",
					"label": "No of Bags",
					"insert_after": "custom_outturn_number",
					"depends_on": _GATE_ITEM,
					"non_negative": 1,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_no_of_pockets",
					"fieldtype": "Float",
					"label": "Pockets (kg)",
					"insert_after": "custom_no_of_bags",
					"depends_on": _GATE_ITEM,
					"non_negative": 1,
					"module": "Upande Coffee",
				},
			],
		},
		ignore_validate=True,
	)
	remove_legacy_fields()


def remove_legacy_fields():
	"""Drop fields from the old delivery-type gating if present."""
	legacy = [
		"Delivery Note-custom_outturn_references",
		"Delivery Note-custom_is_coffee_dispatch",
		"Delivery Note-custom_coffee_transport_section",
		"Delivery Note-custom_coffee_driver_name",
		"Delivery Note-custom_coffee_number_plate",
		"Delivery Note-custom_coffee_col_break",
		"Delivery Note-custom_coffee_transporter",
		"Delivery Note-custom_movement_permit_number",
		"Delivery Note Item-custom_grower",
	]
	for name in legacy:
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
