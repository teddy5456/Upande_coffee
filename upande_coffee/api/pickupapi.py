# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Weighbridge operations: enter weights on Harvest Pickups and drive the
# Harvest Pickup Flow workflow from the coffee web app.

import json

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions


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
def workflow_action(name, action):
	doc = frappe.get_doc("Harvest Pickup", name)
	doc = apply_workflow(doc, action)
	return {"name": doc.name, "workflow_state": doc.workflow_state, "docstatus": doc.docstatus}
