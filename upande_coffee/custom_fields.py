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
			# ── Sales Order (Coffee tab: outgrower service billing) ────────
			# The Tab Break is inserted AFTER the last field of the standard
			# Sales Order (currently `connections_tab`) so it becomes an
			# additional tab at the far right. Nothing standard — items,
			# taxes, accounting dimensions including `business_unit` — moves
			# into our tab. `business_unit` lives in its normal accounting-
			# dimensions section and stays reachable, so it can actually
			# trigger the tab to appear.
			#
			# Visibility is controlled by `depends_on` alone (business_unit
			# contains "endebess"). Setting `hidden=1` as the base state was
			# a mistake — it persists in the field's df and Frappe's
			# depends_on evaluation doesn't override a persistent hidden=1,
			# so the tab never appeared. Standard Frappe pattern is
			# depends_on only; the client JS (public/js/sales_order.js)
			# additionally force-toggles as an extra safety belt.
			"Sales Order": [
				{
					"fieldname": "custom_endebess_tab",
					"fieldtype": "Tab Break",
					"label": "Coffee",
					"insert_after": "connections_tab",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_endebess_intake_sec",
					"fieldtype": "Section Break",
					"label": "Grower Intake",
					"insert_after": "custom_endebess_tab",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_outturn_number",
					"fieldtype": "Data",
					"label": "Outturn Number",
					"insert_after": "custom_endebess_intake_sec",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"read_only": 1,
					"unique": 1,
					"description": "Auto-generated on first save. Feeds the Pick List for parchment intake and matches the Outturn Statement filed after milling.",
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_grower_code",
					"fieldtype": "Data",
					"label": "Grower Code",
					"insert_after": "custom_outturn_number",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"fetch_from": "customer.custom_grower_code",
					"read_only": 1,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_expected_bags",
					"fieldtype": "Int",
					"label": "Expected Bags",
					"insert_after": "custom_grower_code",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"non_negative": 1,
					"description": "60 kg bags the grower is expected to bring.",
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_expected_parchment_weight_kg",
					"fieldtype": "Float",
					"label": "Expected Parchment Weight (kg)",
					"insert_after": "custom_expected_bags",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"non_negative": 1,
					"precision": 2,
					"description": "Drives Milling and Handling service qtys on the items table.",
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_endebess_col_break",
					"fieldtype": "Column Break",
					"insert_after": "custom_expected_parchment_weight_kg",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_transport_expenses",
					"fieldtype": "Check",
					"label": "Include Transport",
					"insert_after": "custom_endebess_col_break",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"description": "Tick to add a Transport service line ($3/60-kg bag of output).",
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_source_booking",
					"fieldtype": "Link",
					"label": "Migrated From Booking",
					"options": "Booking",
					"insert_after": "custom_transport_expenses",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"read_only": 1,
					"description": "Set by the Booking → Sales Order backfill script (Phase 4). Audit only.",
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_endebess_parchment_sec",
					"fieldtype": "Section Break",
					"label": "Expected Parchment Types",
					"insert_after": "custom_source_booking",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "custom_parchment_types",
					"fieldtype": "Table",
					"label": "Parchment Types",
					"options": "Endebess Parchment Type",
					"insert_after": "custom_endebess_parchment_sec",
					"hidden": 0,
					"depends_on": _GATE_HDR,
					"description": "Expected parchment types (P1, P2, Mbuni…). Used by the Material Receipt in Phase 3.",
					"module": "Upande Coffee",
				},
			],
			# Work Order is intentionally NOT modified. Internal Endebess
			# milling is booked via Sales Order to an internal Customer
			# (is_internal_customer=1) — same Coffee tab, no billing needed.
			#
			# Pick List is intentionally NOT modified. Coffee parchment intake
			# runs through the dedicated `Coffee Intake` doctype (added in
			# upande_coffee/upande_coffee/doctype/coffee_intake/) — Pick
			# List's built-in validations don't fit the "receive from outside
			# for outgrowers, transfer per-bin for internal" use case.
			# ── Outturn Statement (link back to source Coffee SO) ────────────
			# outturn_number itself now fetches from
			# custom_source_sales_order.custom_outturn_number (see the
			# outturn_statement.json field definition) — no separate
			# display field needed.
			"Outturn Statement": [
				{
					"fieldname": "custom_source_sales_order",
					"fieldtype": "Link",
					"label": "Source Sales Order",
					"options": "Sales Order",
					"insert_after": "outturn_type",
					"description": "Coffee SO this outturn was milled for. Setting this fetches the outturn number and pins the audit chain SO → Coffee Intake → Outturn.",
					"module": "Upande Coffee",
				},
			],
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
			# Which drying table a moisture Quality Inspection was taken on.
			# Gated to coffee templates (all named "Coffee …") so non-coffee
			# inspections never see it. Standard Frappe depends_on — no hidden=1
			# base state (see the Sales Order note above for why that breaks).
			"Quality Inspection": [
				{
					"fieldname": "custom_drying_table",
					"fieldtype": "Link",
					"label": "Drying Table",
					"options": "Drying Table",
					"insert_after": "batch_no",
					"depends_on": "eval:(doc.quality_inspection_template||'').startsWith('Coffee ')",
					"module": "Upande Coffee",
				},
			],
			# Idempotency key sent by the Kahawa Trail app: "deviceId:queueRowId".
			# The app queues every scan in SQLite and uploads it later; when an
			# upload times out it cannot know whether the insert committed, so it
			# looks the ref up before retrying and adopts the existing log rather
			# than logging the same bucket twice. Indexed because that lookup runs
			# on every retried row. Blank for desk-entered logs.
			"Harvest Log": [
				{
					"fieldname": "client_ref",
					"fieldtype": "Data",
					"label": "Client Ref",
					"insert_after": "bucket_count",
					"read_only": 1,
					"no_copy": 1,
					"search_index": 1,
					"module": "Upande Coffee",
				},
				# Where the scan happened, captured by the app at scan time (not
				# at upload time — an offline scan may sync hours later from the
				# office). Absent when the clerk declined location permission or
				# the device had no recent fix; treat blank as "unknown", never
				# as "not in the block".
				{
					"fieldname": "gps_section",
					"fieldtype": "Section Break",
					"label": "Scan Location",
					"insert_after": "client_ref",
					"collapsible": 1,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "gps_latitude",
					"fieldtype": "Float",
					"label": "Latitude",
					"precision": "6",
					"insert_after": "gps_section",
					"read_only": 1,
					"no_copy": 1,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "gps_longitude",
					"fieldtype": "Float",
					"label": "Longitude",
					"precision": "6",
					"insert_after": "gps_latitude",
					"read_only": 1,
					"no_copy": 1,
					"module": "Upande Coffee",
				},
				{
					"fieldname": "gps_accuracy_m",
					"fieldtype": "Float",
					"label": "GPS Accuracy (m)",
					"precision": "1",
					"insert_after": "gps_longitude",
					"read_only": 1,
					"no_copy": 1,
					"module": "Upande Coffee",
				},
			],
			# Expo push tokens registered by the Kahawa Trail app, newline-
			# separated, one line per device. Written by the
			# kahawa_register_push_token endpoint and read by the Comment hook
			# that pushes support replies. Safe to clear at any time — devices
			# re-register on next launch.
			"User": [
				{
					"fieldname": "custom_expo_push_tokens",
					"fieldtype": "Small Text",
					"label": "Expo Push Tokens",
					"insert_after": "mute_sounds",
					"read_only": 1,
					"no_copy": 1,
					"module": "Upande Coffee",
				},
			],
		},
		ignore_validate=True,
	)
	remove_legacy_fields()


def remove_legacy_fields():
	"""Drop fields from the old delivery-type gating and from an earlier
	pass that briefly installed a Coffee tab + Business Unit link on Work
	Order. Internal Endebess milling now goes through Sales Order to an
	internal Customer, so the WO-side surface was removed."""
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
		# Work Order Coffee tab + Business Unit link — pulled after the
		# switch to Sales-Order-for-internal.
		"Work Order-custom_business_unit",
		"Work Order-custom_endebess_tab",
		"Work Order-custom_endebess_intake_sec",
		"Work Order-custom_expected_bags",
		"Work Order-custom_expected_parchment_weight_kg",
		"Work Order-custom_endebess_col_break",
		"Work Order-custom_outturn_statement",
		"Work Order-custom_endebess_parchment_sec",
		"Work Order-custom_parchment_types",
		# Outturn ↔ Work Order back-link.
		"Outturn Statement-custom_work_order",
		# Pick List Coffee intake — replaced by dedicated Coffee Intake doctype.
		"Pick List-custom_coffee_sec",
		"Pick List-custom_sales_order",
		"Pick List-custom_outturn_number",
		"Pick List-custom_intake_stock_entry",
		# Redundant SO outturn display field — outturn_number itself now fetches
		# from the source SO.
		"Outturn Statement-custom_source_outturn_number",
	]
	for name in legacy:
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)


# Fields other Upande apps make mandatory on shared doctypes. They were written
# when Roses was the only business unit on the site, so they are reqd=1 with no
# condition and block every Coffee document. Scoping them to Roses leaves the
# Roses forms exactly as they were (same red asterisk, same client-side block).
_FOREIGN_MANDATORY = {
	"Sales Order": [
		"custom_shipping_agent",
		"custom_delivery_point",
		"custom_consignee",
		# Owned by upande_packhouse, reqd=1 with no condition at all — blocks
		# every Coffee SO save (confirmed live against real Endebess SOs).
		"custom_order_name",
	],
}
_ROSES_ONLY = 'eval: doc.business_unit == "Roses"'

# custom_s_number / custom_truck_details are ALSO Packhouse fields, already
# "scoped" to Roses — but their condition references `custom_business_unit`,
# which has never existed on Sales Order (the real field is `business_unit`),
# so the eval is always undefined/falsy in the desk form and, empirically,
# still enforced server-side too — Coffee SOs get blocked by them exactly as
# if they had no condition at all. Not an operator's intentional override to
# preserve: a condition that can never reference a real field is a bug, so
# fix the reference in place rather than route it through the "no condition"
# path above (which would skip these since mandatory_depends_on is already set).
_BROKEN_BUSINESS_UNIT_REF = "custom_business_unit"
_FIX_BUSINESS_UNIT_REF = [
	("Sales Order", "custom_s_number"),
	("Sales Order", "custom_truck_details"),
]


def relax_foreign_mandatory_fields():
	"""Scope another app's unconditional mandatory fields to Roses.

	Runs on after_migrate so it re-asserts itself if the owning app re-syncs
	its fixtures. Only touches fields that are still unconditionally mandatory
	— an operator who sets their own condition keeps it.
	"""
	for doctype, fieldnames in _FOREIGN_MANDATORY.items():
		for fieldname in fieldnames:
			name = f"{doctype}-{fieldname}"
			if not frappe.db.exists("Custom Field", name):
				continue

			current = frappe.db.get_value(
				"Custom Field", name, ["reqd", "mandatory_depends_on"], as_dict=True
			)
			if not current.reqd or current.mandatory_depends_on:
				continue

			frappe.db.set_value(
				"Custom Field",
				name,
				{"reqd": 0, "mandatory_depends_on": _ROSES_ONLY},
				update_modified=False,
			)
			frappe.clear_cache(doctype=doctype)

	for doctype, fieldname in _FIX_BUSINESS_UNIT_REF:
		name = f"{doctype}-{fieldname}"
		if not frappe.db.exists("Custom Field", name):
			continue
		current = frappe.db.get_value(
			"Custom Field", name, ["reqd", "mandatory_depends_on"], as_dict=True
		)
		condition = current.mandatory_depends_on or ""
		# Keyed off reqd, not the exact wording of the condition, so this stays
		# idempotent whether the condition still has the broken field
		# reference, was already corrected by a prior run, or is missing
		# entirely — reqd=1 is the actual problem in every one of those cases.
		if current.reqd:
			fixed_condition = (
				condition.replace(_BROKEN_BUSINESS_UNIT_REF, "business_unit")
				if _BROKEN_BUSINESS_UNIT_REF in condition
				else (condition or _ROSES_ONLY)
			)
			frappe.db.set_value(
				"Custom Field",
				name,
				{"reqd": 0, "mandatory_depends_on": fixed_condition},
				update_modified=False,
			)
			frappe.clear_cache(doctype=doctype)
