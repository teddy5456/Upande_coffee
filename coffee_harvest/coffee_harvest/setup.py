import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	"Delivery Note": [
		{
			"fieldname": "custom_movement_permit_number",
			"fieldtype": "Data",
			"label": "Movement Permit Number",
			"insert_after": "lr_no",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_outturn_references",
			"fieldtype": "Small Text",
			"label": "Outturn References",
			"description": "Comma-separated outturn numbers included in this delivery",
			"insert_after": "custom_movement_permit_number",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_is_coffee_dispatch",
			"fieldtype": "Check",
			"label": "Coffee Dispatch",
			"insert_after": "custom_outturn_references",
			"default": "0",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_coffee_transport_section",
			"fieldtype": "Section Break",
			"label": "Coffee Transport Details",
			"insert_after": "custom_is_coffee_dispatch",
			"depends_on": "eval:doc.custom_is_coffee_dispatch == 1",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_coffee_driver_name",
			"fieldtype": "Data",
			"label": "Driver Name",
			"insert_after": "custom_coffee_transport_section",
			"depends_on": "eval:doc.custom_is_coffee_dispatch == 1",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_coffee_number_plate",
			"fieldtype": "Data",
			"label": "Number Plate",
			"insert_after": "custom_coffee_driver_name",
			"depends_on": "eval:doc.custom_is_coffee_dispatch == 1",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_coffee_col_break",
			"fieldtype": "Column Break",
			"insert_after": "custom_coffee_number_plate",
			"depends_on": "eval:doc.custom_is_coffee_dispatch == 1",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_coffee_transporter",
			"fieldtype": "Data",
			"label": "Transporter",
			"insert_after": "custom_coffee_col_break",
			"depends_on": "eval:doc.custom_is_coffee_dispatch == 1",
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_coffee_seal_number",
			"fieldtype": "Data",
			"label": "Seal / Container No.",
			"insert_after": "custom_coffee_transporter",
			"depends_on": "eval:doc.custom_is_coffee_dispatch == 1",
			"module": "Coffee Harvest",
		},
	],
	"Delivery Note Item": [
		{
			"fieldname": "custom_outturn_number",
			"fieldtype": "Data",
			"label": "Outturn No.",
			"insert_after": "item_name",
			"in_list_view": 1,
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_grower",
			"fieldtype": "Data",
			"label": "Grower",
			"insert_after": "custom_outturn_number",
			"in_list_view": 1,
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_no_of_bags",
			"fieldtype": "Int",
			"label": "Bags (60 kg)",
			"insert_after": "custom_grower",
			"in_list_view": 1,
			"module": "Coffee Harvest",
		},
		{
			"fieldname": "custom_no_of_pockets",
			"fieldtype": "Int",
			"label": "Pockets (kg)",
			"insert_after": "custom_no_of_bags",
			"in_list_view": 1,
			"module": "Coffee Harvest",
		},
	],
	"Sales Invoice": [
		{
			"fieldname": "custom_outturn_number",
			"fieldtype": "Data",
			"label": "Outturn Number",
			"insert_after": "po_no",
			"module": "Coffee Harvest",
		},
	],
}


def after_install():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	_ensure_roles()


def _ensure_roles():
	for role in ("Coffee Harvest Manager", "Coffee Harvest User"):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
