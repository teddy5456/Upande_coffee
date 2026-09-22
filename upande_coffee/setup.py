# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe

from upande_coffee.custom_fields import create_coffee_custom_fields


COFFEE_LOGO = "/assets/upande_coffee/images/coffee-logo.png"


def after_install():
	create_coffee_custom_fields()
	_ensure_roles()
	set_coffee_desktop_icon()


def set_coffee_desktop_icon():
	"""Use the coffee logo image as the desk sidebar icon (Desktop Icons are
	regenerated on migrate, so re-apply the logo each time)."""
	for name in frappe.get_all("Desktop Icon", filters={"label": "Coffee"}, pluck="name"):
		frappe.db.set_value(
			"Desktop Icon", name,
			{"logo_url": COFFEE_LOGO, "icon_image": COFFEE_LOGO},
			update_modified=False,
		)


def _ensure_roles():
	for role in ("Coffee Harvest Manager", "Coffee Harvest User"):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
