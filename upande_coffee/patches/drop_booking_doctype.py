"""Drop the Booking doctype.

Outgrowers now go through Sales Order (custom_outturn_number on the Coffee
tab); internal Endebess milling uses the same Sales Order flow with an
`is_internal_customer=1` Customer. Booking is fully retired.

Runs pre-model-sync so the DocType record + tabBooking table are gone
before Frappe tries to reconcile the disk state (where the folder no
longer exists). Idempotent — safe to re-run."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Booking"):
		return

	# Nuke child data first. Frappe's cascade will drop related child rows,
	# but be explicit about the two known references to keep migration
	# logs quiet.
	if frappe.db.table_exists("Booking"):
		frappe.db.sql("DELETE FROM `tabBooking`")

	# force=True bypasses the "linked to other docs" check — dead-code
	# fields elsewhere still point at Booking, but they're being scrubbed
	# in the same migrate cycle.
	frappe.delete_doc("DocType", "Booking", force=True, ignore_permissions=True)
	frappe.db.commit()
