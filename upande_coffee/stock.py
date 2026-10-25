# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe


def apply_coffee_defaults(se):
	"""Give every row of a coffee Stock Entry its cost centre and difference
	account. Kaitet Ltd. has no company default cost centre or stock adjustment
	account, so without these each coffee repack failed on submit -- dried
	batches could not go to the bins from 2026-10-08. Coffee Settings wins; the
	company defaults are the fallback for sites that do set them."""
	settings = frappe.get_cached_doc("Coffee Settings")
	cost_center = settings.get("stock_cost_center") or frappe.get_cached_value("Company", se.company, "cost_center")
	account = settings.get("stock_adjustment_account") or frappe.get_cached_value(
		"Company", se.company, "stock_adjustment_account")
	for row in se.items:
		row.cost_center = row.get("cost_center") or cost_center
		row.expense_account = row.get("expense_account") or account
	return se
