# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Quality for the coffee web + mobile apps, on native ERPNext Quality
# Inspection. There are no custom quality doctypes: a moisture reading, a
# floaters check and an outturn screen-size count are all the same thing — a
# Quality Inspection against a template — and the template is what differs.
#
# Deliberately template-DRIVEN rather than parameter-hardcoded. `get_templates`
# hands the client the parameters with their acceptance limits, and
# `record_inspection` accepts whatever readings come back. Adding a sixth
# parameter to a template in the desk therefore needs no API change and no app
# release, which is the whole reason this file replaces per-parameter endpoints.

import json

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from upande_coffee.api.dryingapi import _batch_stock_entry


# Which context a template needs, i.e. what the client must ask for in order to
# identify the coffee. Kept HERE rather than in the app so that retiring the
# drying-table box from a template, or adding a Sales Order box to a new one, is
# a server change and needs no app release.
#
# Why they differ (the stages genuinely don't share an identity):
#   drying      — a batch sitting on a named table
#   stores      — item + bin; the batch is often not carried into the stores
#   outturn     — taken DURING milling, so the {outturn}-{grade} batches do not
#                 exist yet; the Sales Order and the parchment in the mill are
#                 all there is
#   dispatch    — belongs on the Delivery Note, so ERPNext's own
#                 inspection_required_before_delivery gate can see it
TEMPLATE_CONTEXT = {
	"Coffee Cherry Intake": ["batch", "warehouse"],
	"Coffee Drying": ["batch", "drying_table"],
	"Coffee Parchment (Pre-Mill)": ["item", "warehouse", "batch"],
	"Coffee Outturn": ["sales_order", "item"],
	"Coffee Dispatch": ["delivery_note", "item"],
	"Coffee Cupping": ["sales_order", "item"],
}
# Anything the officer creates that we have no opinion about: the general case.
DEFAULT_CONTEXT = ["item", "warehouse", "batch"]


@frappe.whitelist()
def get_templates():
	"""Every Quality Inspection Template with its parameters, limits and context.

	Returns all templates rather than a coffee-only allowlist: a template added
	in the desk should appear in the app by itself. `numeric` decides whether
	the client shows a number field or a free-text/select one, and min/max drive
	the inline pass-fail hint before the record is even saved. `context` tells
	the client which identity pickers to show — see TEMPLATE_CONTEXT.
	"""
	out = []
	for name in frappe.get_all("Quality Inspection Template", pluck="name", order_by="name"):
		doc = frappe.get_doc("Quality Inspection Template", name)
		out.append({
			"template": name,
			"context": TEMPLATE_CONTEXT.get(name, DEFAULT_CONTEXT),
			"parameters": [
				{
					"specification": r.specification,
					"parameter_group": r.parameter_group,
					"numeric": int(r.numeric or 0),
					"min_value": flt(r.min_value),
					"max_value": flt(r.max_value),
					"value": r.value,
					"formula_based_criteria": int(r.formula_based_criteria or 0),
					"acceptance_formula": r.acceptance_formula,
				}
				for r in doc.item_quality_inspection_parameter
			],
		})
	return out


def _item_warehouse_stock_entry(item_code, warehouse):
	"""Latest Stock Entry that moved this item in or out of this warehouse.

	The batchless fallback. Coffee does not always keep its batch once it is
	moved into the stores, so an inspection taken there can only be identified
	by item + store — there is no batch to hang it on. The Stock Entry that put
	the coffee there is still a truthful reference for the Quality Inspection.
	"""
	rows = frappe.db.sql(
		"""SELECT sle.voucher_no
		FROM `tabStock Ledger Entry` sle
		WHERE sle.voucher_type = 'Stock Entry' AND sle.is_cancelled = 0
		  AND sle.item_code = %(i)s AND sle.warehouse = %(w)s
		ORDER BY sle.posting_date DESC, sle.creation DESC LIMIT 1""",
		{"i": item_code, "w": warehouse},
	)
	return rows[0][0] if rows else None


def _dry_mill_stock_entry(sales_order=None):
	"""The milling run's input: the latest Stock Entry that put parchment into
	the dry mill.

	Needed because an outturn inspection is taken DURING milling, when the
	{outturn}-{grade} batches do not exist yet — the Outturn Statement creates
	them on submit. Sales Order is not an allowed reference_type either, so the
	parchment transfer is the closest truthful document.

	If a Sales Order is given and it already has a submitted Outturn Statement,
	that statement's own Repack is preferred: by then the grades really exist.
	"""
	settings = frappe.get_cached_doc("Coffee Settings")
	dry_mill = settings.get("dry_mill_warehouse")

	if sales_order:
		outturn = frappe.db.get_value(
			"Outturn Statement",
			{"custom_source_sales_order": sales_order, "docstatus": 1},
			"name",
		)
		if outturn:
			# The Repack the outturn produced, found via the batches it created.
			rows = frappe.db.sql(
				"""SELECT sle.voucher_no
				FROM `tabStock Ledger Entry` sle
				LEFT JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
				WHERE sle.voucher_type = 'Stock Entry' AND sle.is_cancelled = 0
				  AND COALESCE(sbe.batch_no, sle.batch_no) LIKE %(pat)s
				ORDER BY sle.posting_date DESC, sle.creation DESC LIMIT 1""",
				{"pat": f"{outturn}-%"},
			)
			if rows:
				return rows[0][0]

	if not dry_mill:
		return None
	rows = frappe.db.sql(
		"""SELECT sed.parent
		FROM `tabStock Entry Detail` sed
		JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE se.docstatus = 1 AND sed.t_warehouse = %(w)s
		ORDER BY se.posting_date DESC, se.creation DESC LIMIT 1""",
		{"w": dry_mill},
	)
	return rows[0][0] if rows else None


def _resolve_reference(
	batch=None,
	reference_type=None,
	reference_name=None,
	item_code=None,
	warehouse=None,
	sales_order=None,
	delivery_note=None,
):
	"""Quality Inspection insists on a stock/subcontract document reference.

	A field reading — moisture on a drying table, floaters in a store — has no
	such document of its own, so we derive one, in descending order of how
	specifically it identifies the coffee:

	  1. an explicit reference the caller already knows
	  2. the Stock Entry that last moved the batch
	  3. the Stock Entry that last moved this item into this warehouse
	     (for stock whose batch was not carried into the stores)

	Batch is therefore optional, not required.
	"""
	if reference_type and reference_name:
		if not frappe.db.exists(reference_type, reference_name):
			frappe.throw(_("{0} {1} not found.").format(reference_type, reference_name))
		return reference_type, reference_name

	# Dispatch: the inspection belongs ON the Delivery Note, so that ERPNext's
	# inspection_required_before_delivery gate and DN Item.quality_inspection can
	# both see it. Anything else would leave the gate blind.
	if delivery_note:
		if not frappe.db.exists("Delivery Note", delivery_note):
			frappe.throw(_("Delivery Note {0} not found.").format(delivery_note))
		return "Delivery Note", delivery_note

	# Outturn taken during milling: no grade batch exists yet.
	if sales_order and not batch:
		se = _dry_mill_stock_entry(sales_order)
		if se:
			return "Stock Entry", se
		frappe.throw(
			_("No milling Stock Entry found for {0}. Transfer parchment to the dry mill first.").format(sales_order)
		)

	if batch:
		se = _batch_stock_entry(batch)
		if se:
			return "Stock Entry", se
		# Fall through to the item+warehouse route rather than dead-ending: a
		# batch with no Stock Entry of its own is exactly the case where the
		# store movement is the only record.

	if item_code and warehouse:
		se = _item_warehouse_stock_entry(item_code, warehouse)
		if se:
			return "Stock Entry", se
		frappe.throw(
			_("No Stock Entry found moving {0} into {1} to reference.").format(item_code, warehouse)
		)

	if sales_order:
		se = _dry_mill_stock_entry(sales_order)
		if se:
			return "Stock Entry", se

	if batch:
		frappe.throw(
			_("No Stock Entry found for batch {0}. Pass a warehouse, or an explicit reference.").format(batch)
		)

	frappe.throw(
		_("Nothing to reference. Provide a batch, an item and warehouse, a Sales Order, or a Delivery Note.")
	)


@frappe.whitelist()
def get_inspectable_stock(warehouse=None):
	"""What is physically there to inspect: item + warehouse + batch + qty.

	Unlike dryingapi.batch_stock this deliberately KEEPS batchless lines, since
	coffee in the stores may have no batch and still needs inspecting. `batch_no`
	comes back empty for those, and the client leaves the batch unset.
	"""
	conds = ["sle.is_cancelled = 0"]
	params = {}
	if warehouse:
		conds.append("sle.warehouse = %(w)s")
		params["w"] = warehouse
	return frappe.db.sql(
		"""SELECT sle.warehouse,
			sle.item_code,
			COALESCE(sbe.batch_no, sle.batch_no, '') AS batch_no,
			SUM(COALESCE(sbe.qty, sle.actual_qty)) AS qty
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = sle.serial_and_batch_bundle
		WHERE {conds}
		GROUP BY sle.warehouse, sle.item_code, COALESCE(sbe.batch_no, sle.batch_no, '')
		HAVING qty > 0
		ORDER BY sle.warehouse, sle.item_code""".format(conds=" AND ".join(conds)),
		params,
		as_dict=True,
	)


@frappe.whitelist(methods=["POST"])
def record_inspection(
	template,
	readings,
	batch=None,
	item_code=None,
	warehouse=None,
	drying_table=None,
	sales_order=None,
	delivery_note=None,
	reference_type=None,
	reference_name=None,
	report_date=None,
	sample_size=1,
	inspection_type=None,
	remarks=None,
):
	"""Record a Quality Inspection against any template.

	`readings` is {specification: value} — e.g. {"Moisture %": 11.4} or
	{"Floaters %": 2, "Foreign Matter %": 0.5}. Limits are copied from the
	template rather than trusted from the client, so a device cannot widen its
	own acceptance range; ERPNext's own validate() then sets each reading's
	Accepted/Rejected status and the document status from those limits.

	Specifications not present in the template are rejected outright — a typo in
	a client would otherwise create a reading that no report ever sums.
	"""
	if isinstance(readings, str):
		readings = json.loads(readings)
	if not isinstance(readings, dict) or not readings:
		frappe.throw(_("At least one reading is required."))

	if not frappe.db.exists("Quality Inspection Template", template):
		frappe.throw(_("Quality Inspection Template {0} not found.").format(template))

	tpl = frappe.get_doc("Quality Inspection Template", template)
	by_spec = {r.specification: r for r in tpl.item_quality_inspection_parameter}

	unknown = [s for s in readings if s not in by_spec]
	if unknown:
		frappe.throw(
			_("{0} is not a parameter of template {1}. Expected any of: {2}").format(
				", ".join(unknown), template, ", ".join(by_spec) or "(none)"
			)
		)

	if not item_code and batch:
		item_code = frappe.db.get_value("Batch", batch, "item")
	if not item_code:
		frappe.throw(_("An item is required, or a batch it can be read from."))

	ref_type, ref_name = _resolve_reference(
		batch, reference_type, reference_name, item_code, warehouse, sales_order, delivery_note
	)

	# A dispatch check is Outgoing, everything else is In Process. Left
	# overridable, but defaulting it means the client never has to know.
	if not inspection_type:
		inspection_type = "Outgoing" if ref_type in ("Delivery Note", "Sales Invoice") else "In Process"

	# ERPNext hard-refuses a Delivery Note / Sales Invoice inspection unless the
	# item is flagged for it, and its own message ("no need to create the QI")
	# reads like the inspection is pointless rather than like a missing setting.
	# Say what to actually do — this is the one prerequisite a dispatch check has.
	if ref_type in ("Delivery Note", "Sales Invoice") and not frappe.get_cached_value(
		"Item", item_code, "inspection_required_before_delivery"
	):
		frappe.throw(
			_(
				"Item {0} does not have <b>Inspection Required before Delivery</b> enabled, "
				"so ERPNext will not accept a dispatch inspection for it. Enable it on the Item "
				"(Quality section). Note that with Stock Settings set to Stop, this will also "
				"block Delivery Notes for {0} until a passing inspection exists."
			).format(item_code)
		)
	if ref_type in ("Purchase Receipt", "Purchase Invoice") and not frappe.get_cached_value(
		"Item", item_code, "inspection_required_before_purchase"
	):
		frappe.throw(
			_(
				"Item {0} does not have <b>Inspection Required before Purchase</b> enabled, "
				"so ERPNext will not accept an incoming inspection for it."
			).format(item_code)
		)

	rows = []
	for spec, raw in readings.items():
		param = by_spec[spec]
		row = {
			"specification": spec,
			"parameter_group": param.parameter_group,
			"numeric": int(param.numeric or 0),
			# Limits come from the template, never from the caller.
			"min_value": flt(param.min_value),
			"max_value": flt(param.max_value),
			"formula_based_criteria": int(param.formula_based_criteria or 0),
			"acceptance_formula": param.acceptance_formula,
		}
		if param.numeric:
			# ERPNext grades numeric readings from reading_1..reading_10 ONLY
			# (see QualityInspection.min_max_criteria_passed) — and if none of
			# them is set it returns False, i.e. Rejected, whatever the value.
			# reading_value is a display/reporting field, so write both: the
			# dashboards read reading_value, the grading reads reading_1.
			row["reading_1"] = str(flt(raw))
			row["reading_value"] = flt(raw)
		else:
			# Non-numeric parameters compare the observed text against `value`.
			row["reading_value"] = str(raw)
			row["value"] = param.value
		rows.append(row)

	qi = frappe.get_doc({
		"doctype": "Quality Inspection",
		"inspection_type": inspection_type,
		"report_date": report_date or nowdate(),
		"reference_type": ref_type,
		"reference_name": ref_name,
		"item_code": item_code,
		"batch_no": batch,
		"sample_size": flt(sample_size) or 1,
		# NOT manual_inspection: that flag tells ERPNext to leave the document
		# status alone, so a rejected reading would sit inside an "Accepted"
		# inspection. Letting it roll up is the point — 19% moisture must
		# produce a Rejected inspection, not a green one with a red row.
		"inspected_by": frappe.session.user,
		"quality_inspection_template": template,
		"custom_drying_table": drying_table,
		"remarks": remarks,
		"readings": rows,
	})
	qi.insert()

	return {
		"name": qi.name,
		"status": qi.status,
		"template": template,
		"readings": [
			{"specification": r.specification, "reading_value": r.reading_value, "status": r.status}
			for r in qi.readings
		],
	}


@frappe.whitelist()
def get_context_options():
	"""Everything the identity pickers need, in one round trip.

	One call rather than four, because this runs on a phone on a farm
	connection. Lists are capped — the officer picks from what is live, not from
	the whole history.
	"""
	settings = frappe.get_cached_doc("Coffee Settings")

	return {
		"stock": get_inspectable_stock(),
		"drying_tables": frappe.get_all(
			"Drying Table",
			fields=["name", "table_id", "status", "current_batch"],
			order_by="name",
			limit_page_length=200,
		),
		# Open orders only: you inspect what is being milled now.
		"sales_orders": frappe.get_all(
			"Sales Order",
			filters={"docstatus": 1, "status": ["not in", ["Closed", "Completed", "Cancelled"]]},
			fields=["name", "customer", "transaction_date"],
			order_by="transaction_date desc",
			limit_page_length=60,
		),
		# Drafts included: the whole point is to inspect before it goes out.
		"delivery_notes": _delivery_notes_with_items(),
		# Grades and parchment types, for the stages where the coffee has no
		# stock yet (an outturn grade does not exist until milling finishes).
		"items": frappe.get_all(
			"Item",
			filters={"item_group": ["like", "%offee%"], "disabled": 0},
			fields=["name", "item_name"],
			order_by="name",
			limit_page_length=200,
		),
		"dry_mill": settings.get("dry_mill_warehouse"),
		"milled_store": settings.get("milled_store_warehouse"),
	}


def _delivery_notes_with_items():
	"""Recent Delivery Notes, each carrying its own item codes.

	The items are attached so a dispatch inspection can only be filed against
	something actually on that note — picking a grade that is not being
	delivered would produce a Quality Inspection nobody ever looks at. Two
	queries, not one per note.
	"""
	notes = frappe.get_all(
		"Delivery Note",
		filters={"docstatus": ["<", 2]},
		fields=["name", "customer", "posting_date", "docstatus"],
		order_by="posting_date desc, creation desc",
		limit_page_length=60,
	)
	if not notes:
		return []
	rows = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": ["in", [n.name for n in notes]]},
		fields=["parent", "item_code"],
		limit_page_length=0,
	)
	by_note = {}
	for r in rows:
		by_note.setdefault(r.parent, [])
		if r.item_code not in by_note[r.parent]:
			by_note[r.parent].append(r.item_code)
	for n in notes:
		n["items"] = by_note.get(n.name, [])
	return notes


@frappe.whitelist()
def list_inspections(template=None, from_date=None, to_date=None, drying_table=None, batch=None, limit=60):
	"""Recent inspections with their readings flattened, newest first.

	One query for the parents and one for all their readings — not one per
	parent — because this feeds a phone on a farm connection.
	"""
	filters = {"docstatus": ["<", 2]}
	if template:
		filters["quality_inspection_template"] = template
	if drying_table:
		filters["custom_drying_table"] = drying_table
	if batch:
		filters["batch_no"] = batch
	if from_date and to_date:
		filters["report_date"] = ["between", [from_date, to_date]]
	elif from_date:
		filters["report_date"] = [">=", from_date]
	elif to_date:
		filters["report_date"] = ["<=", to_date]

	parents = frappe.get_all(
		"Quality Inspection",
		filters=filters,
		fields=[
			"name", "report_date", "status", "quality_inspection_template",
			"item_code", "batch_no", "custom_drying_table", "inspected_by", "remarks",
		],
		order_by="report_date desc, creation desc",
		limit_page_length=frappe.utils.cint(limit) or 60,
	)
	if not parents:
		return []

	reading_rows = frappe.get_all(
		"Quality Inspection Reading",
		filters={"parent": ["in", [p.name for p in parents]]},
		fields=["parent", "specification", "reading_value", "status", "min_value", "max_value", "numeric"],
		order_by="idx asc",
		limit_page_length=0,
	)
	by_parent = {}
	for r in reading_rows:
		by_parent.setdefault(r.parent, []).append({
			"specification": r.specification,
			"reading_value": r.reading_value,
			"status": r.status,
			"min_value": flt(r.min_value),
			"max_value": flt(r.max_value),
			"numeric": int(r.numeric or 0),
		})

	for p in parents:
		p["readings"] = by_parent.get(p.name, [])
	return parents
