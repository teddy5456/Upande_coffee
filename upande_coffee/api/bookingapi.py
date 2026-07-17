"""bookingapi — compatibility shim.

The Booking doctype was retired; outgrowers now flow through Sales Order
and internal Endebess milling uses the same SO with `is_internal_customer`.
The coffee-dashboard UI (`upande_coffee/www/coffee-dashboard.html`) still
calls the old six endpoints, so this module keeps their shapes intact
while proxying to the current SO + Coffee Intake flow.

Endpoint map:
  get_defaults       → identical (no booking data touched)
  list_bookings      → lists Coffee Sales Orders, mapped to legacy shape
  create_booking     → creates a Coffee Sales Order (Draft)
  submit_booking     → submits the SO
  pending_transfers  → SOs submitted but with no Coffee Intake yet
  transfer_to_mill   → creates + submits a Coffee Intake for the SO
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate


# ─────────────────────────────────────────────────────────────────────────
# Choices for the New Booking form
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_defaults():
	"""Growers, parchment types, bins, today's date. Unchanged shape."""
	return {
		"parchment_types": frappe.get_all(
			"Parchment Type", pluck="name", order_by="name",
		),
		"growers": frappe.get_all(
			"Customer", filters={"disabled": 0},
			fields=["name", "customer_name"],
			order_by="customer_name", limit_page_length=200,
		),
		"bins": frappe.get_all(
			"Warehouse",
			filters={
				"is_group": 0, "disabled": 0,
				"custom_farm": ["like", "%endebess%"],
				"warehouse_type": ["not in", ["Greenhouse", "Block", "Transit"]],
			},
			fields=["name"], order_by="name", limit_page_length=200,
		),
		"today": nowdate(),
	}


# ─────────────────────────────────────────────────────────────────────────
# Status derivation (SO → legacy Booking status labels)
# ─────────────────────────────────────────────────────────────────────────

def _so_status(so_row):
	"""Legacy Booking exposed: Draft / Booked / Transferred / Completed.
	Map from the SO's docstatus + presence of a Coffee Intake / Outturn."""
	if so_row.get("docstatus") == 0:
		return "Draft"
	if not so_row.get("has_intake"):
		return "Booked"
	if not so_row.get("has_outturn"):
		return "Transferred"
	return "Completed"


def _so_intake_map(so_names):
	"""Return {so_name: intake_name_or_None} for a batch of SO names."""
	if not so_names:
		return {}
	rows = frappe.get_all(
		"Coffee Intake",
		filters={"sales_order": ["in", so_names], "docstatus": 1},
		fields=["name", "sales_order", "intake_stock_entry"],
	)
	return {r.sales_order: r for r in rows}


def _so_outturn_map(so_names):
	"""Return {so_name: outturn_name_or_None} for a batch of SO names."""
	if not so_names:
		return {}
	rows = frappe.get_all(
		"Outturn Statement",
		filters={
			"custom_source_sales_order": ["in", so_names],
			"docstatus": 1,
		},
		fields=["name", "custom_source_sales_order"],
	)
	return {r.custom_source_sales_order: r.name for r in rows}


def _so_first_parchment_type(so_names):
	"""First parchment type per SO (legacy showed one type per booking)."""
	if not so_names:
		return {}
	rows = frappe.get_all(
		"Endebess Parchment Type",
		filters={"parent": ["in", so_names], "parenttype": "Sales Order"},
		fields=["parent", "parchment_type"],
		order_by="parent asc, idx asc",
	)
	out = {}
	for r in rows:
		out.setdefault(r.parent, r.parchment_type)
	return out


def _map_so_to_booking_row(so, intake_map, outturn_map, parchment_map):
	"""Reshape an SO record into the legacy Booking row the UI expects."""
	intake = intake_map.get(so.name)
	so["has_intake"] = bool(intake)
	so["has_outturn"] = so.name in outturn_map
	return {
		"name":            so.name,
		"outturn_number":  so.get("custom_outturn_number") or so.name,
		"grower":          so.get("customer"),
		"grower_code":     so.get("custom_grower_code") or "",
		"parchment_type":  parchment_map.get(so.name) or "",
		"no_of_bags":      int(so.get("custom_expected_bags") or 0),
		"bag_weight":      0.06,  # legacy showed empty-bag tare; no equivalent on SO
		"net_weight":      flt(so.get("custom_expected_parchment_weight_kg")),
		"booking_date":    so.get("transaction_date"),
		"status":          _so_status(so),
		"docstatus":       so.get("docstatus", 0),
		"source_bin":      "",   # first parchment row's source_warehouse — see pending_transfers
	}


# ─────────────────────────────────────────────────────────────────────────
# Bookings list — reads Coffee SOs
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_bookings(limit=50):
	"""All Coffee SOs (those with a custom_outturn_number), mapped to the
	legacy Booking row shape."""
	sos = frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": ["<", 2],
			"custom_outturn_number": ["is", "set"],
		},
		fields=[
			"name", "docstatus", "customer", "transaction_date",
			"custom_outturn_number", "custom_grower_code",
			"custom_expected_bags", "custom_expected_parchment_weight_kg",
		],
		order_by="transaction_date desc, creation desc",
		limit_page_length=int(limit),
	)
	names = [s.name for s in sos]
	intake_map = _so_intake_map(names)
	outturn_map = _so_outturn_map(names)
	parchment_map = _so_first_parchment_type(names)
	return [
		_map_so_to_booking_row(so, intake_map, outturn_map, parchment_map)
		for so in sos
	]


# ─────────────────────────────────────────────────────────────────────────
# Create a booking → creates a Draft Coffee Sales Order
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def create_booking(parchment_type, no_of_bags, gross_weight,
					grower=None, bag_weight=0.6, booking_date=None,
					source_bin=None, outturn_number=None):
	"""Create a Draft Sales Order for a coffee booking.

	Args mirror the legacy Booking create — bag_weight is the empty-bag
	tare, so net_weight = gross - bags × tare. On the new SO we store net
	weight as `custom_expected_parchment_weight_kg` and #bags as
	`custom_expected_bags`, plus one parchment-types row with the given
	parchment_type + source_bin. The `custom_outturn_number` auto-fills
	via the SO's before_validate hook (WWEM##### series)."""
	no_of_bags = int(no_of_bags or 0)
	gross_weight = flt(gross_weight)
	bag_weight = flt(bag_weight)
	net_weight = max(0.0, gross_weight - (no_of_bags * bag_weight))

	if not grower:
		frappe.throw(_("Grower (Customer) is required."))
	if not parchment_type:
		frappe.throw(_("Parchment Type is required."))

	so = frappe.new_doc("Sales Order")
	so.customer = grower
	so.transaction_date = booking_date or nowdate()
	so.delivery_date = booking_date or nowdate()

	# Business unit — force Endebess so the Coffee tab is enabled + the
	# before_validate hook stamps the outturn number.
	# (If a non-Endebess business unit is desired, ops can change post-create.)
	if not so.get("business_unit") and not so.get("custom_business_unit"):
		endebess_bu = frappe.db.get_value(
			"Business Unit", {"name": ["like", "%Endebess%"]}, "name",
		)
		if endebess_bu:
			try:
				so.business_unit = endebess_bu
			except Exception:
				so.custom_business_unit = endebess_bu

	so.custom_expected_bags = no_of_bags
	so.custom_expected_parchment_weight_kg = net_weight
	if outturn_number:
		so.custom_outturn_number = outturn_number  # explicit override

	so.append("custom_parchment_types", {
		"parchment_type":     parchment_type,
		"expected_bags":      no_of_bags,
		"expected_weight_kg": net_weight,
		"source_warehouse":   source_bin or None,
	})

	so.insert()
	return {
		"name":       so.name,
		"status":     _so_status({
			"docstatus": so.docstatus, "has_intake": False, "has_outturn": False,
		}),
		"net_weight": net_weight,
	}


# ─────────────────────────────────────────────────────────────────────────
# Submit a draft booking → submits the SO
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def submit_booking(name):
	if not frappe.db.exists("Sales Order", name):
		frappe.throw(_("Sales Order {0} not found.").format(name))
	so = frappe.get_doc("Sales Order", name)
	if so.docstatus != 0:
		frappe.throw(_("Sales Order {0} is already submitted or cancelled.").format(name))
	so.submit()
	return {"name": so.name, "docstatus": so.docstatus}


# ─────────────────────────────────────────────────────────────────────────
# Pending transfers — SOs submitted but with no Coffee Intake yet
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def pending_transfers():
	sos = frappe.get_all(
		"Sales Order",
		filters={"docstatus": 1, "custom_outturn_number": ["is", "set"]},
		fields=[
			"name", "customer as grower", "transaction_date as booking_date",
			"custom_expected_bags as no_of_bags",
			"custom_expected_parchment_weight_kg as net_weight",
			"custom_outturn_number",
		],
		order_by="transaction_date asc, creation asc",
		limit_page_length=100,
	)
	if not sos:
		return []
	names = [s.name for s in sos]
	intake_map = _so_intake_map(names)
	parchment_map = _so_first_parchment_type(names)

	# First parchment row's source_warehouse per SO (for the UI's bin display).
	source_rows = frappe.get_all(
		"Endebess Parchment Type",
		filters={"parent": ["in", names], "parenttype": "Sales Order"},
		fields=["parent", "source_warehouse"],
		order_by="parent asc, idx asc",
	)
	source_map = {}
	for r in source_rows:
		if r.source_warehouse and r.parent not in source_map:
			source_map[r.parent] = r.source_warehouse

	pending = []
	for so in sos:
		if so.name in intake_map:
			continue  # already transferred
		pending.append({
			"name":               so.name,
			"grower":             so.grower,
			"parchment_type":     parchment_map.get(so.name) or "",
			"no_of_bags":         int(so.no_of_bags or 0),
			"net_weight":         flt(so.net_weight),
			"source_bin":         source_map.get(so.name) or "",
			"current_bin_stock":  0,   # legacy field; no equivalent computed here
			"booking_date":       so.booking_date,
			"status":             "Booked",
		})
	return pending


# ─────────────────────────────────────────────────────────────────────────
# Transfer to mill → creates + submits a Coffee Intake
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def transfer_to_mill(name, source_bin=None):
	"""Create + submit a Coffee Intake for this SO, applying source_bin as
	the source_warehouse on every row (used only for internal transfers;
	outgrower intakes ignore it). Returns the intake name + the resulting
	Stock Entry so the UI's toast message stays intact."""
	if not frappe.db.exists("Sales Order", name):
		frappe.throw(_("Sales Order {0} not found.").format(name))

	# Idempotency — surface the existing intake instead of double-posting.
	existing = frappe.db.get_value(
		"Coffee Intake",
		{"sales_order": name, "docstatus": 1},
		["name", "intake_stock_entry"], as_dict=True,
	)
	if existing:
		return {
			"name": name,
			"stock_entry": existing.intake_stock_entry,
			"status": "Transferred",
		}

	intake = frappe.new_doc("Coffee Intake")
	intake.sales_order = name
	intake.posting_date = nowdate()

	# Pre-populate items from the SO's parchment types (same helper the
	# desk form uses).
	from upande_coffee.upande_coffee.doctype.coffee_intake.coffee_intake import (
		get_intake_rows_from_sales_order,
	)
	for spec in get_intake_rows_from_sales_order(name):
		wh = source_bin or spec.get("source_warehouse")
		intake.append("items", {
			"parchment_type":   spec.get("parchment_type"),
			"qty_kg":           spec.get("qty_kg"),
			"source_warehouse": wh,
		})

	if not intake.items:
		frappe.throw(_("SO {0} has no parchment types — nothing to transfer.").format(name))

	intake.insert(ignore_permissions=True)
	intake.submit()

	return {
		"name":        name,
		"stock_entry": intake.intake_stock_entry,
		"status":      "Transferred",
	}
