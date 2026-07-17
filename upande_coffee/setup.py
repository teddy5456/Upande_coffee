# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe

from upande_coffee.custom_fields import create_coffee_custom_fields


def after_install():
	create_coffee_custom_fields()
	_ensure_roles()


def _ensure_roles():
	for role in ("Coffee Harvest Manager", "Coffee Harvest User"):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
