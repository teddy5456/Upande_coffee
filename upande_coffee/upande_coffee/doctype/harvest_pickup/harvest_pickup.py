# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from upande_coffee.stock import apply_coffee_defaults


class HarvestPickup(Document):
	def before_insert(self):
		self._claim_logs()

	def on_trash(self):
		_release_logs(self)

	def _claim_logs(self):
		"""Creating the pickup takes its buckets off the block there and then:
		the day's unpicked logs, oldest first, are marked picked_up and listed
		on the row. Before this the app marked them afterwards with one
		set_value per log, which failed silently on big blocks (1,000 parallel
		calls) and left the block offering the same buckets -- and weight --
		to a second pickup. FOR UPDATE stops two phones claiming the same logs."""
		taken = set()
		for row in self.block_pickups:
			if row.picked_log_ids or not row.block or not row.bucket_count:
				continue
			logs = frappe.db.sql(
				"""SELECT name, bucket_count FROM `tabHarvest Log`
				WHERE block = %s AND date = %s AND picked_up = 0
				ORDER BY creation FOR UPDATE""",
				(row.block, self.date), as_dict=True,
			)
			logs = [l for l in logs if l.name not in taken]
			available = sum(l.bucket_count or 0 for l in logs)
			if row.bucket_count > available:
				frappe.throw(_("Row {0}: {1} has only {2} bucket(s) left to pick up on {3}, not {4}. "
					"They may already be on another pickup.").format(
					row.idx, row.block, int(available), frappe.format(self.date, "Date"), row.bucket_count))
			chosen, left = [], row.bucket_count
			for l in logs:
				if left <= 0:
					break
				chosen.append(l.name)
				left -= l.bucket_count or 0
			taken.update(chosen)
			row.picked_log_ids = ",".join(chosen)
		if taken:
			frappe.db.sql("UPDATE `tabHarvest Log` SET picked_up = 1 WHERE name IN %s", (tuple(taken),))

	def validate(self):
		# Totals first: weighbridge validation reads total_weight_kg, which is
		# stale (often 0) when weights arrive via an API round-trip save.
		self._calculate_totals()
		self._validate_weighbridge()
		self._warn_on_post_submit_correction()

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

	def _warn_on_post_submit_correction(self):
		"""Bucket/weight fields are allow_on_submit so supervisors can correct a
		weighbridge mistake after Receive. If stock already moved on the old
		total, flag it — the Stock Entry created on submit is not auto-adjusted."""
		if self.docstatus == 1 and self.moved_stock and not self.is_new():
			old_weight = frappe.db.get_value("Harvest Pickup", self.name, "total_weight_kg")
			if old_weight is not None and flt(old_weight) != flt(self.total_weight_kg):
				frappe.msgprint(
					_(
						"Total weight changed from {0} kg to {1} kg after stock was already moved "
						"({2}). Adjust the stock entry manually if needed."
					).format(flt(old_weight), flt(self.total_weight_kg), self.stock_entry or ""),
					indicator="orange",
					alert=True,
				)


def on_submit_create_stock_entry(doc, method):
	"""Create stock entry moving cherry from blocks to the wet mill when the
	pickup is Received (workflow submits the document).

	Moves the mill re-weigh (received_weight_kg) when it was captured by the
	receiving step, since that reflects what actually arrived (spillage/theft
	in transit can differ from the field weighbridge figure); falls back to
	total_weight_kg for a doc submitted without going through receive_pickup."""
	if doc.moved_stock:
		return
	qty = flt(doc.received_weight_kg) or flt(doc.total_weight_kg)
	if not qty or qty <= 0:
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
	# Without this, Frappe silently overwrites posting_date with the current
	# moment on insert (see StockController) — invisible when Receive happens
	# same-day as the pickup, but every pickup approved a day or more later
	# posted its cherry on the approval date instead of the pickup date.
	se.set_posting_time = 1
	se.posting_time = "12:00:00"
	se.company = company
	se.remarks = f"Cherry received from harvest pickup {doc.name} on {doc.date}"
	se.append(
		"items",
		{
			"item_code": settings.cherry_item,
			"qty": qty,
			"uom": "Kilogram",
			"t_warehouse": settings.wet_mill_warehouse,
			"batch_no": batch_name,
			"use_serial_batch_fields": 1,
			"allow_zero_valuation_rate": 1,
		},
	)
	apply_coffee_defaults(se)
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value("Harvest Pickup", doc.name, "moved_stock", 1, update_modified=False)
	frappe.db.set_value("Harvest Pickup", doc.name, "stock_entry", se.name, update_modified=False)
	frappe.msgprint(
		_("Stock Entry {0} created: {1} kg cherry moved to {2}.").format(
			se.name, qty, settings.wet_mill_warehouse
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
	_release_logs(doc)


def _release_logs(doc):
	"""Give a cancelled / deleted pickup's buckets back to their block."""
	ids = tuple(i.strip() for row in doc.block_pickups for i in (row.picked_log_ids or "").split(",") if i.strip())
	if ids:
		frappe.db.sql("UPDATE `tabHarvest Log` SET picked_up = 0 WHERE name IN %s", (ids,))
