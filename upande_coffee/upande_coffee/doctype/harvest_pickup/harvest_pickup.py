# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HarvestPickup(Document):
	def validate(self):
		# Totals first: weighbridge validation reads total_weight_kg, which is
		# stale (often 0) when weights arrive via an API round-trip save.
		self._calculate_totals()
		self._validate_weighbridge()

	def _validate_weighbridge(self):
		"""Weights become mandatory once the weigh approval happens
		(workflow state Weighed) and stay mandatory through Received."""
		if self.get("workflow_state") in ("Weighed", "Received"):
			for row in self.block_pickups:
				if not row.weight_kg or row.weight_kg <= 0:
					frappe.throw(
						_("Row {0}: Weight (kg) is required once the pickup is weighed.").format(row.idx)
					)
			if not self.total_weight_kg or self.total_weight_kg <= 0:
				frappe.throw(_("Total weight must be greater than 0 once the pickup is weighed."))

	def _calculate_totals(self):
		self.total_buckets = sum(row.bucket_count or 0 for row in self.block_pickups)
		self.total_weight_kg = sum(row.weight_kg or 0 for row in self.block_pickups)


def on_submit_create_stock_entry(doc, method):
	"""Create stock entry moving cherry from blocks to the wet mill when the
	pickup is Received (workflow submits the document)."""
	if doc.moved_stock:
		return
	if not doc.total_weight_kg or doc.total_weight_kg <= 0:
		frappe.throw(_("Cannot receive: total weight must be greater than 0."))

	settings = frappe.get_cached_doc("Coffee Settings")
	if not settings.cherry_item or not settings.wet_mill_warehouse:
		frappe.throw(
			_("Set Cherry Item and Wet Mill Warehouse in Coffee Settings before receiving pickups.")
		)
	company = frappe.db.get_value("Warehouse", settings.wet_mill_warehouse, "company")

	# Mark harvest logs as picked up
	for row in doc.block_pickups:
		if row.picked_log_ids:
			log_ids = [lid.strip() for lid in row.picked_log_ids.split(",") if lid.strip()]
			for log_id in log_ids:
				if frappe.db.exists("Harvest Log", log_id):
					frappe.db.set_value("Harvest Log", log_id, "picked_up", 1, update_modified=False)

	# Create or reuse a date-level cherry batch
	batch_name = f"CHERRY-{doc.date}"
	if not frappe.db.exists("Batch", batch_name):
		batch = frappe.new_doc("Batch")
		batch.batch_id = batch_name
		batch.item = settings.cherry_item
		batch.insert(ignore_permissions=True)

	# Stock entry: Material Receipt into the wet mill
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Receipt"
	se.posting_date = doc.date
	se.company = company
	se.remarks = f"Cherry received from harvest pickup {doc.name} on {doc.date}"
	se.append(
		"items",
		{
			"item_code": settings.cherry_item,
			"qty": doc.total_weight_kg,
			"uom": "Kilogram",
			"t_warehouse": settings.wet_mill_warehouse,
			"batch_no": batch_name,
			"use_serial_batch_fields": 1,
			"allow_zero_valuation_rate": 1,
		},
	)
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value("Harvest Pickup", doc.name, "moved_stock", 1, update_modified=False)
	frappe.db.set_value("Harvest Pickup", doc.name, "stock_entry", se.name, update_modified=False)
	frappe.msgprint(
		_("Stock Entry {0} created: {1} kg cherry moved to {2}.").format(
			se.name, doc.total_weight_kg, settings.wet_mill_warehouse
		),
		indicator="green",
	)


def on_cancel_reverse_stock_entry(doc, method):
	"""Cancel the stock entry if pickup is cancelled."""
	if doc.stock_entry and frappe.db.exists("Stock Entry", doc.stock_entry):
		se = frappe.get_doc("Stock Entry", doc.stock_entry)
		if se.docstatus == 1:
			se.cancel()
	frappe.db.set_value("Harvest Pickup", doc.name, "moved_stock", 0, update_modified=False)
	for row in doc.block_pickups:
		if row.picked_log_ids:
			log_ids = [lid.strip() for lid in row.picked_log_ids.split(",") if lid.strip()]
			for log_id in log_ids:
				if frappe.db.exists("Harvest Log", log_id):
					frappe.db.set_value("Harvest Log", log_id, "picked_up", 0, update_modified=False)
