# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Booking operations for the coffee web app.

import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def get_defaults():
	"""Choices needed by the booking form."""
	return {
		"parchment_types": frappe.get_all("Parchment Type", pluck="name", order_by="name"),
		"growers": frappe.get_all("Customer", filters={"disabled": 0},
			fields=["name", "customer_name"], order_by="customer_name", limit_page_length=200),
		"bins": frappe.get_all("Warehouse",
			filters={"is_group": 0, "disabled": 0, "custom_farm": ["like", "%endebess%"],
					"warehouse_type": ["not in", ["Greenhouse", "Block", "Transit"]]},
			fields=["name"], order_by="name", limit_page_length=200),
		"today": nowdate(),
	}


@frappe.whitelist()
def list_bookings(limit=50):
	return frappe.get_all(
		"Booking",
		filters={"docstatus": ["<", 2]},
		fields=["name", "outturn_number", "grower", "grower_code", "parchment_type",
				"no_of_bags", "bag_weight", "net_weight", "booking_date", "status", "docstatus"],
		order_by="booking_date desc, creation desc",
		limit_page_length=int(limit),
	)


@frappe.whitelist(methods=["POST"])
def create_booking(parchment_type, no_of_bags, gross_weight, grower=None,
					bag_weight=0.6, booking_date=None, source_bin=None, outturn_number=None):
	"""bag_weight is the EMPTY bag tare; the controller computes
	net = gross - bags x tare. Outturn number auto-generates as
	{week}EM{seq} from the active season when left blank."""
	doc = frappe.get_doc({
		"doctype": "Booking",
		"grower": grower,
		"parchment_type": parchment_type,
		"no_of_bags": int(no_of_bags),
		"bag_weight": flt(bag_weight),
		"gross_weight": flt(gross_weight),
		"booking_date": booking_date or nowdate(),
		"source_bin": source_bin,
		"outturn_number": outturn_number,
	}).insert()
	return {"name": doc.name, "status": doc.status, "net_weight": doc.net_weight}


@frappe.whitelist()
def pending_transfers():
	"""Submitted bookings whose parchment has not yet moved to the dry mill."""
	return frappe.get_all(
		"Booking",
		filters={"docstatus": 1, "transfer_stock_entry": ["is", "not set"]},
		fields=["name", "grower", "parchment_type", "no_of_bags", "net_weight",
				"source_bin", "current_bin_stock", "booking_date", "status"],
		order_by="booking_date asc",
		limit_page_length=100,
	)


@frappe.whitelist(methods=["POST"])
def transfer_to_mill(name, source_bin=None):
	from upande_coffee.upande_coffee.doctype.booking.booking import transfer_booking_to_mill

	doc = frappe.get_doc("Booking", name)
	if source_bin and not doc.source_bin:
		# bin decided at transfer time (booking was submitted without one)
		frappe.db.set_value("Booking", name, "source_bin", source_bin, update_modified=False)
		doc.reload()
	se = transfer_booking_to_mill(doc)
	return {"name": name, "stock_entry": se, "status": "Transferred"}


@frappe.whitelist(methods=["POST"])
def submit_booking(name):
	doc = frappe.get_doc("Booking", name)
	if doc.docstatus != 0:
		frappe.throw(_("Booking {0} is already submitted or cancelled.").format(name))
	doc.submit()
	return {"name": doc.name, "docstatus": doc.docstatus}
