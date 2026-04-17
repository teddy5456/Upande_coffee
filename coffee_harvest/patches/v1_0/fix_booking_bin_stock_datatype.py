"""
Patch: fix_booking_bin_stock_datatype

The Booking doctype had `current_bin_stock` stored as varchar(140) in the
legacy custom doctype. The app definition changes it to Float (decimal 21,9)
which requires NOT NULL DEFAULT 0. Any existing NULL values must be zeroed
out before the column ALTER can succeed.

Also normalises the `status` column which had NULL values on some rows.
"""

import frappe


def execute():
	# Zero out any NULL or empty-string values that would block the decimal NOT NULL column migration.
	# Frappe Data fields store '' (empty string) as the default, not NULL, so both cases must be handled.
	frappe.db.sql(
		"UPDATE `tabBooking` SET current_bin_stock = 0 WHERE current_bin_stock IS NULL OR current_bin_stock = ''"
	)
	frappe.db.sql("UPDATE `tabBooking` SET cafe = 0 WHERE cafe IS NULL")
	frappe.db.sql("UPDATE `tabBooking` SET ra = 0 WHERE ra IS NULL")
	frappe.db.sql(
		"UPDATE `tabBooking` SET status = 'Booked' WHERE (status IS NULL OR status = '') AND docstatus = 1"
	)
	frappe.db.commit()
