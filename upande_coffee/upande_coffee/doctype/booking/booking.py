# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


def _settings():
	s = frappe.get_cached_doc("Coffee Settings")
	if not s.parchment_item or not s.dry_mill_warehouse:
		frappe.throw(
			_("Set Parchment Item and Dry Mill Warehouse in Coffee Settings before transferring bookings.")
		)
	return s


def parchment_item_for(parchment_type=None):
	"""Item a parchment type is stored as: Parchment Type.item, else an item
	named like the type, else the Coffee Settings default."""
	if parchment_type:
		item = frappe.db.get_value("Parchment Type", parchment_type, "item")
		if item:
			return item
		if frappe.db.exists("Item", parchment_type):
			return parchment_type
	return frappe.get_cached_doc("Coffee Settings").parchment_item


def generate_outturn_number(booking_date=None):
	"""Auto outturn number: {season week}EM{per-week sequence}, e.g. 15EM0001.
	The week counts from the active Coffee Season's Week One Start."""
	from frappe.utils import date_diff, getdate, nowdate

	season = frappe.db.get_value(
		"Coffee Season", {"is_active": 1},
		["name", "week_one_start", "start_date"], as_dict=True,
	)
	if not season:
		frappe.throw(_("No active Coffee Season — set one (with Week One Start) to auto-number outturns."))
	anchor = season.week_one_start or season.start_date
	if not anchor:
		frappe.throw(_("Coffee Season {0} has no Week One Start date.").format(season.name))
	days = date_diff(getdate(booking_date or nowdate()), getdate(anchor))
	if days < 0:
		frappe.throw(_("Booking date is before Week One Start of season {0}.").format(season.name))
	week = days // 7 + 1

	prefix = f"{week}EM"
	last = frappe.db.sql(
		"""SELECT outturn_number FROM `tabBooking`
		WHERE outturn_number LIKE %s ORDER BY outturn_number DESC LIMIT 1""",
		f"{prefix}%",
	)
	seq = 1
	if last:
		try:
			seq = int(last[0][0][len(prefix):]) + 1
		except ValueError:
			seq = 1
	return f"{prefix}{seq:04d}"


class Booking(Document):
	def before_naming(self):
		if not self.outturn_number:
			self.outturn_number = generate_outturn_number(self.booking_date)

	def validate(self):
		self._compute_net_weight()
		self._fetch_bin_stock()

	def before_submit(self):
		if self.status == "Transferred":
			self._validate_transfer()
		elif not self.status or self.status == "Draft":
			self.status = "Booked"

	def _compute_net_weight(self):
		"""bag_weight is the EMPTY bag tare: net = gross - bags x tare."""
		if self.gross_weight:
			tare = (self.no_of_bags or 0) * (self.bag_weight or 0)
			self.net_weight = max(self.gross_weight - tare, 0)

	def _fetch_bin_stock(self):
		parchment = parchment_item_for(self.parchment_type)
		if self.source_bin and parchment:
			self.current_bin_stock = (
				frappe.db.get_value(
					"Bin",
					{"warehouse": self.source_bin, "item_code": parchment},
					"actual_qty",
				)
				or 0
			)

	def _validate_transfer(self):
		if self.grower:
			# outgrower coffee arrives from outside — no source bin needed
			return
		if not self.source_bin:
			frappe.throw(_("Source Bin is required to transfer estate coffee to the mill."))
		if not self.net_weight or self.net_weight <= 0:
			frappe.throw(_("Net Weight is required to transfer to the mill."))
		self._fetch_bin_stock()
		if self.net_weight > (self.current_bin_stock or 0):
			frappe.throw(
				_("Insufficient stock in {0}. Available: {1} kg, Required: {2} kg.").format(
					self.source_bin, self.current_bin_stock, self.net_weight
				)
			)


def transfer_booking_to_mill(doc):
	"""Bring a booked parchment lot into the dry mill.

	Outgrower booking (grower set): the coffee arrives from outside — a
	Material Receipt straight into the dry mill, batch named by the outturn.
	Estate booking (grower blank): a Material Transfer from the source bin,
	batches allocated FIFO."""
	if doc.docstatus != 1:
		frappe.throw(_("Booking {0} must be submitted before transfer.").format(doc.name))
	if doc.transfer_stock_entry:
		frappe.throw(_("Booking {0} was already transferred ({1}).").format(doc.name, doc.transfer_stock_entry))
	if not doc.net_weight:
		frappe.throw(_("Net Weight is required to transfer to mill."))
	is_outgrower = bool(doc.grower)
	if not is_outgrower and not doc.source_bin:
		frappe.throw(_("Source Bin is required to transfer estate coffee to mill."))

	settings = _settings()
	item = parchment_item_for(doc.parchment_type)
	se = frappe.new_doc("Stock Entry")
	se.posting_date = doc.transfer_date or frappe.utils.today()
	se.company = frappe.db.get_value("Warehouse", settings.dry_mill_warehouse, "company")

	if is_outgrower:
		se.stock_entry_type = "Material Receipt"
		se.remarks = f"Outgrower parchment received at dry mill for outturn {doc.outturn_number} ({doc.grower})"
		batch = doc.outturn_number
		if frappe.db.get_value("Item", item, "has_batch_no") and not frappe.db.exists("Batch", batch):
			frappe.get_doc({"doctype": "Batch", "batch_id": batch,
							"item": item}).insert(ignore_permissions=True)
		se.append(
			"items",
			{
				"item_code": item,
				"qty": doc.net_weight,
				"uom": "Kilogram",
				"t_warehouse": settings.dry_mill_warehouse,
				"batch_no": batch,
				"use_serial_batch_fields": 1,
				"allow_zero_valuation_rate": 1,
			},
		)
	else:
		se.stock_entry_type = "Material Transfer"
		se.remarks = f"Estate parchment transfer to dry mill for outturn {doc.outturn_number}"
		for batch_no, qty in _allocate_batches(item, doc.source_bin, doc.net_weight):
			se.append(
				"items",
				{
					"item_code": item,
					"qty": qty,
					"uom": "Kilogram",
					"s_warehouse": doc.source_bin,
					"t_warehouse": settings.dry_mill_warehouse,
					"batch_no": batch_no,
					"use_serial_batch_fields": 1,
					"allow_zero_valuation_rate": 1,
				},
			)
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value("Booking", doc.name, {
		"transfer_stock_entry": se.name,
		"transfer_date": se.posting_date,
		"status": "Transferred",
	}, update_modified=False)
	return se.name


def _allocate_batches(item_code, warehouse, required_qty, prefer_batch=None):
	"""FIFO batch allocation for a batched item in one warehouse, optionally
	consuming prefer_batch first. Returns [(batch_no, qty)] covering
	required_qty; for non-batched items a single unbatched row."""
	from frappe.utils import flt

	if not frappe.db.get_value("Item", item_code, "has_batch_no"):
		return [(None, flt(required_qty))]

	rows = frappe.db.sql(
		"""SELECT COALESCE(sbe.batch_no, sle.batch_no) AS batch_no,
			SUM(COALESCE(sbe.qty, sle.actual_qty)) AS qty, MIN(sle.posting_date) first_seen
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
		WHERE sle.warehouse = %s AND sle.item_code = %s AND sle.is_cancelled = 0
		GROUP BY COALESCE(sbe.batch_no, sle.batch_no)
		HAVING batch_no IS NOT NULL AND qty > 0
		ORDER BY first_seen, batch_no""",
		(warehouse, item_code),
		as_dict=True,
	)
	if prefer_batch:
		rows.sort(key=lambda r: 0 if r.batch_no == prefer_batch else 1)
	remaining = flt(required_qty)
	alloc = []
	for r in rows:
		if remaining <= 0:
			break
		take = min(flt(r.qty), remaining)
		alloc.append((r.batch_no, take))
		remaining -= take
	if remaining > 0.01:
		frappe.throw(
			_("Insufficient batched stock of {0} in {1}: need {2} kg, found {3} kg.").format(
				item_code, warehouse, flt(required_qty), flt(required_qty) - remaining
			)
		)
	return alloc


def on_submit_transfer_to_mill(doc, method):
	"""On submit: bookings normally wait as 'Booked' (transfer happens from
	the Transfers page). If submitted already marked Transferred, move now."""
	if doc.status not in ("Transferred", "Milling", "Completed"):
		frappe.db.set_value("Booking", doc.name, "status", "Booked", update_modified=False)
		return
	if not doc.transfer_stock_entry:
		transfer_booking_to_mill(doc)


def on_cancel_reverse_transfer(doc, method):
	"""Cancel the transfer stock entry on booking cancel."""
	if doc.transfer_stock_entry and frappe.db.exists("Stock Entry", doc.transfer_stock_entry):
		se = frappe.get_doc("Stock Entry", doc.transfer_stock_entry)
		if se.docstatus == 1:
			se.cancel()
	frappe.db.set_value("Booking", doc.name, "transfer_stock_entry", None, update_modified=False)
