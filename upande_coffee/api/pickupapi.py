# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Weighbridge operations: enter weights on Harvest Pickups and drive the
# Harvest Pickup Flow workflow from the coffee web app.

import json

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import cint, flt, getdate, now_datetime, nowdate


@frappe.whitelist()
def pending_pickups():
	"""Open pickups with their block rows and the workflow actions the
	current user may take on each."""
	pickups = frappe.get_all(
		"Harvest Pickup",
		filters={"docstatus": 0},
		fields=["name", "date", "total_buckets", "total_weight_kg", "workflow_state"],
		order_by="date desc, creation desc",
		limit_page_length=50,
	)
	for p in pickups:
		doc = frappe.get_doc("Harvest Pickup", p.name)
		p["rows"] = [
			{"name": r.name, "block": r.block, "bucket_count": r.bucket_count, "weight_kg": r.weight_kg}
			for r in doc.block_pickups
		]
		try:
			p["actions"] = [t.action for t in get_transitions(doc)]
		except Exception:
			p["actions"] = []
	return pickups


@frappe.whitelist()
def get_tractors(search=None, asset_category="Tractors", limit=500):
	"""Options for the Harvest Pickup ``tractor`` Link field (→ Asset).

	Returns Assets in the "Tractors" category, minus disposed ones
	(Scrapped/Sold/Cancelled). Pass a different ``asset_category`` to override,
	or ``asset_category=""`` for all assets. ``search`` does a typeahead match
	on the asset id / name. Consumed by the Kahawa Trail harvest-pickup dropdown.
	"""
	filters = {"status": ["not in", ["Scrapped", "Sold", "Cancelled"]]}
	if asset_category:
		filters["asset_category"] = asset_category

	or_filters = None
	if search:
		like = "%{0}%".format(search)
		or_filters = {"name": ["like", like], "asset_name": ["like", like]}

	return frappe.get_all(
		"Asset",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "asset_name", "asset_category", "status"],
		order_by="asset_name asc",
		limit_page_length=frappe.utils.cint(limit) or 500,
	)


@frappe.whitelist(methods=["POST"])
def save_weights(name, weights):
	"""weights: JSON map of child row name -> weight in kg."""
	weights = json.loads(weights) if isinstance(weights, str) else weights
	doc = frappe.get_doc("Harvest Pickup", name)
	if doc.docstatus != 0:
		frappe.throw(_("Pickup {0} is already submitted.").format(name))
	for row in doc.block_pickups:
		if row.name in weights:
			row.weight_kg = frappe.utils.flt(weights[row.name])
	doc.save()
	return {"name": doc.name, "total_weight_kg": doc.total_weight_kg}


@frappe.whitelist(methods=["POST"])
def workflow_action(name, action, weights=None):
	"""Advance the pickup workflow. Any weights typed on the card are saved
	first (mirrors the desk form, where editing rows + clicking the action
	persist together) so validation never trips on stale/blank weights."""
	doc = frappe.get_doc("Harvest Pickup", name)
	if weights and doc.docstatus == 0:
		weights = json.loads(weights) if isinstance(weights, str) else weights
		changed = False
		for row in doc.block_pickups:
			if row.name in weights and weights[row.name] not in (None, ""):
				row.weight_kg = frappe.utils.flt(weights[row.name])
				changed = True
		if changed:
			doc.save()
			doc.reload()
	doc = apply_workflow(doc, action)
	moved = None
	if doc.docstatus == 1:
		moved = frappe.db.get_value("Harvest Pickup", doc.name, "stock_entry")
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"docstatus": doc.docstatus,
		"stock_entry": moved,
	}


# ── Receiving at the mill ────────────────────────────────────────────────────
#
# Weighing (above) happens at pickup, out in the field. Receiving is a
# separate, later confirmation: when the load physically arrives at the wet
# mill, someone re-weighs it and recounts bags, since the field figure can
# differ from what actually arrives (spillage, theft). The "Weighed" ->
# "Received" transition in the Harvest Pickup Flow workflow is what submits
# the document and triggers on_submit_create_stock_entry, so receiving is
# also the moment cherry stock actually moves into the wet mill warehouse —
# there is no separate stock-moving step elsewhere in this flow.


@frappe.whitelist()
def receivable_pickups():
	"""Weighed pickups waiting to be confirmed as arrived at the mill."""
	return frappe.get_all(
		"Harvest Pickup",
		filters={"docstatus": 0, "workflow_state": "Weighed"},
		fields=["name", "date", "tractor", "total_buckets", "total_weight_kg", "workflow_state"],
		order_by="date asc, creation asc",
		limit_page_length=50,
	)


@frappe.whitelist()
def recent_received_pickups(limit=10):
	"""Pickups already confirmed at the mill, most recently received first."""
	return frappe.get_all(
		"Harvest Pickup",
		filters={"docstatus": 1, "received": 1},
		fields=[
			"name", "date", "tractor", "total_buckets", "total_weight_kg", "workflow_state",
			"received", "received_by", "received_at", "received_weight_kg", "received_bag_count",
			"weight_variance_kg",
		],
		order_by="received_at desc",
		limit_page_length=cint(limit) or 10,
	)


@frappe.whitelist(methods=["POST"])
def receive_pickup(name, received_weight_kg, received_bag_count):
	"""Confirm cherry has physically arrived at the wet mill: record the
	re-weighed total and bag count, then drive the "Receive" workflow
	transition, which submits the document and moves stock using this actual
	weight (see on_submit_create_stock_entry)."""
	doc = frappe.get_doc("Harvest Pickup", name)
	if doc.workflow_state != "Weighed":
		frappe.throw(_("Pickup {0} must be Weighed before it can be received.").format(name))
	if doc.received:
		frappe.throw(_("Pickup {0} has already been received.").format(name))

	received_weight_kg = flt(received_weight_kg)
	if received_weight_kg <= 0:
		frappe.throw(_("Received weight must be greater than 0."))

	doc.db_set("received_weight_kg", received_weight_kg, update_modified=False)
	doc.db_set("received_bag_count", cint(received_bag_count), update_modified=False)
	doc.db_set("weight_variance_kg", received_weight_kg - flt(doc.total_weight_kg), update_modified=False)
	doc.db_set("received_by", frappe.session.user, update_modified=False)
	doc.db_set("received_at", now_datetime(), update_modified=False)
	doc.db_set("received", 1, update_modified=False)

	doc = apply_workflow(doc, "Receive")

	# on_submit_create_stock_entry writes stock_entry via frappe.db.set_value,
	# which doesn't touch this in-memory doc — re-read it (mirrors workflow_action above).
	stock_entry = frappe.db.get_value("Harvest Pickup", doc.name, "stock_entry")

	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"docstatus": doc.docstatus,
		"received_at": doc.received_at,
		"received_by": doc.received_by,
		"received_weight_kg": doc.received_weight_kg,
		"received_bag_count": doc.received_bag_count,
		"weight_variance_kg": doc.weight_variance_kg,
		"stock_entry": stock_entry,
	}


# ── Harvester QR ─────────────────────────────────────────────────────────────
#
# "Empty QR Range" prints a batch of blank Harvester cards ahead of time so
# they can be handed out in the field; "Employee Card"/"Employee Table"
# (label_generation.py) print a card already tied to an employee. Neither path
# lets the field link a blank, already-printed card to the employee who ends
# up holding it — that link had to be made by opening the Harvester record on
# desk. These two calls do it from a scan instead.


@frappe.whitelist()
def get_harvester(harvester_id):
	"""Resolve a scanned Harvester QR to its employee link, if any."""
	doc = frappe.db.get_value(
		"Harvester", harvester_id,
		["name", "harvester_id", "employee", "employee_id", "national_id"],
		as_dict=True,
	)
	if not doc:
		frappe.throw(_("No Harvester record for {0}.").format(harvester_id))
	doc["employee_name"] = (
		frappe.db.get_value("Employee", doc.employee, "employee_name") if doc.employee else None
	)
	return doc


@frappe.whitelist(methods=["POST"])
def link_harvester_employee(harvester_id, employee):
	"""Link a scanned (already-printed) Harvester QR to an Employee.

	Refuses to silently steal a card already linked to someone else — that
	needs a deliberate desk edit, not a rescan.
	"""
	name = frappe.db.exists("Harvester", harvester_id)
	if not name:
		frappe.throw(_("No Harvester record for {0}.").format(harvester_id))

	doc = frappe.get_doc("Harvester", name)
	if doc.employee and doc.employee != employee:
		frappe.throw(
			_("{0} is already linked to {1}.").format(harvester_id, doc.employee)
		)
	doc.employee = employee
	doc.save(ignore_permissions=True)
	return {
		"harvester_id": doc.harvester_id,
		"employee": doc.employee,
		"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
	}


# ── Weekly forecast ──────────────────────────────────────────────────────────
#
# The desktop /coffee-production grid already has the full model: a flowering
# forecast, a Coffee Forecast Override for a human to overrule one block-week,
# and a weighbridge actual to compare against (productiongrid.py). These wrap
# that same machinery in a shape a phone screen can use — a plain week_start
# date instead of the grid's ISO week/year vocabulary, and a small window
# instead of the desktop's full 26-week/coffee_dash payload.


@frappe.whitelist(methods=["POST"])
def submit_weekly_forecast(block, week_start, kg_cherry, reason=None):
	"""A picker/supervisor's own weekly cherry estimate for a block.

	Writes the same Coffee Forecast Override the desktop grid writes to, so a
	number set from the field shows up there too instead of living twice.
	"""
	from upande_coffee.api.productiongrid import _iso, set_forecast_cell

	iso_year, iso_week = _iso(getdate(week_start))
	return set_forecast_cell(
		block=block, week=iso_week, year=iso_year, value=kg_cherry,
		reason=reason or "Submitted from Kahawa Trail",
	)


@frappe.whitelist()
def weekly_forecast_vs_actual(block=None, weeks_back=4, weeks_ahead=8):
	"""Expected (model, or a hand-set override) vs actual weighbridge kg,
	week by week, for the mobile forecast-vs-actual card."""
	from upande_coffee.api.productiongrid import _iso, _span, grid_payload

	today = getdate(nowdate())
	iso_year, now_week = _iso(today)
	start_year, start_week = _span(iso_year, now_week - cint(weeks_back), 1)[0]

	payload = grid_payload(
		start_year=start_year, start_week=start_week,
		end_year=iso_year, end_week=now_week + cint(weeks_ahead),
	)
	blocks = payload["blocks"]
	if block:
		blocks = [b for b in blocks if b["greenhouse"] == block]

	return {
		"weeks": payload["week_dates"],
		"blocks": [
			{
				"block": b["greenhouse"],
				"variety": b["variety"],
				"expected": b["weekly"]["revised"],
				"actual": b["weekly"]["actual"],
			}
			for b in blocks
		],
	}


@frappe.whitelist()
def submit_pickup(name):
	"""Submit by name only.

	frappe.client.submit takes a doc dict and reconstructs it via
	frappe.get_doc(dict) -- for a dict holding only {doctype, name} that
	builds a brand-new, entirely blank in-memory document (every other field
	None) rather than loading the real record, so submitting it either fails
	validation outright or hits Frappe's own modified-timestamp conflict
	check. Loading by name first avoids that trap altogether.
	"""
	frappe.get_doc("Harvest Pickup", name).submit()
