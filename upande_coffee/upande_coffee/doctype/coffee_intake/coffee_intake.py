"""Coffee Intake — the parchment-receiving document.

Replaces the fight-the-framework Pick List approach. Semantically:

  - Outgrower (external customer): Material Receipt into the dry mill —
    parchment enters our books from outside, no source warehouse. Rows
    are allowed at zero valuation so we don't book cost on stock we
    don't own (matches the -OG variants used in delivery).
  - Internal customer: Material Transfer into the dry mill — per-row
    source warehouse (P1 from bin1, P2 from bin2, …). Requires each
    row to carry a source_warehouse.

On submit: creates + submits the appropriate Stock Entry and stamps
`intake_stock_entry`. On cancel: cancels the linked Stock Entry."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, nowtime


class CoffeeIntake(Document):
	def validate(self):
		if not self.sales_order:
			frappe.throw(_("Sales Order is required."))
		if not self.items:
			frappe.throw(_("Add at least one parchment row before saving."))
		# is_internal fetches from customer.is_internal_customer, but
		# guard with an explicit read in case the fetch is stale.
		if not self.customer:
			so_customer = frappe.db.get_value("Sales Order", self.sales_order, "customer")
			if so_customer:
				self.customer = so_customer
		if self.customer and self.is_internal is None:
			self.is_internal = frappe.db.get_value(
				"Customer", self.customer, "is_internal_customer"
			) or 0

		# For internal, each row must carry a source warehouse.
		if self.is_internal:
			for row in self.items:
				if flt(row.qty_kg) <= 0:
					continue
				if not row.source_warehouse:
					frappe.throw(_(
						"Row {0} ({1}): source warehouse is required for internal transfers."
					).format(row.idx, row.parchment_type or "?"))

	def on_submit(self):
		self._post_stock_entry()

	def on_cancel(self):
		self._cancel_stock_entry()

	# ── Stock Entry creation ────────────────────────────────────────────

	def _post_stock_entry(self):
		if self.intake_stock_entry:
			# Idempotency safety — should never re-enter but be defensive.
			return

		settings = frappe.get_cached_doc("Coffee Settings")
		target = settings.get("dry_mill_warehouse")
		if not target:
			frappe.throw(_(
				"Set the <b>Dry Mill Warehouse</b> in Coffee Settings before posting a coffee intake."
			))
		if not settings.get("parchment_item"):
			frappe.throw(_(
				"Set the <b>Parchment Item</b> in Coffee Settings before posting a coffee intake."
			))

		# Per-type item resolver — lives in outturn_statement.py.
		# Priority: Parchment Type.item → item named like the type →
		# Coffee Settings.parchment_item as the final fallback. So P1's
		# actual Item Code (e.g. "P1: Coffee Parchment") is used when
		# Parchment Type "P1" has an `item` link, and only rows with no
		# parchment_type set fall back to the generic settings item.
		from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import (
			parchment_item_for,
		)

		se = frappe.new_doc("Stock Entry")
		se.company = self.company or frappe.db.get_value("Sales Order", self.sales_order, "company")
		se.stock_entry_type = "Material Transfer" if self.is_internal else "Material Receipt"
		se.posting_date = self.posting_date or nowdate()
		se.posting_time = nowtime()
		se.remarks = _(
			"Coffee intake {0} (Sales Order {1}, outturn {2})."
		).format(self.name, self.sales_order, self.outturn_number or "?")

		for row in self.items:
			qty = flt(row.qty_kg)
			if qty <= 0:
				continue

			# Resolve THIS row's parchment item from its parchment_type.
			parchment_item = parchment_item_for(row.parchment_type)
			parchment_is_batched = bool(
				frappe.db.get_value("Item", parchment_item, "has_batch_no")
			)

			# Build the list of (batch_no, batch_qty) tuples that add up
			# to this row's qty. For non-batched items → one entry with
			# batch_no=None.
			allocations = self._allocate_row_batches(
				row=row,
				parchment_item=parchment_item,
				parchment_is_batched=parchment_is_batched,
			)

			for batch_no, batch_qty in allocations:
				item_row = {
					"item_code":                 parchment_item,
					"qty":                       batch_qty,
					"t_warehouse":               target,
					"uom":                       "Kg",
					"stock_uom":                 "Kg",
					"conversion_factor":         1,
					# Permissive flag: if the item HAS a valuation rate
					# (moving-average from prior stock, or a rate on the item
					# master), ERPNext uses it. Flag only kicks in when the
					# computed rate is genuinely zero — first-ever Material
					# Receipt / first internal move with no prior history.
					"allow_zero_valuation_rate": 1,
					"description": _("Parchment: {0}").format(
						row.parchment_type or "Unspecified"
					),
				}
				if batch_no:
					item_row["batch_no"] = batch_no
					item_row["use_serial_batch_fields"] = 1
				if self.is_internal:
					item_row["s_warehouse"] = row.source_warehouse
				se.append("items", item_row)

		if not se.get("items"):
			frappe.throw(_("No positive-qty rows on this Coffee Intake — nothing to post."))

		se.insert(ignore_permissions=True)
		se.submit()

		self.db_set("intake_stock_entry", se.name, update_modified=False)

		frappe.msgprint(
			_("Coffee intake posted: Stock Entry <a href='/app/stock-entry/{0}'>{0}</a> "
			  "({1}, {2} row{3}).").format(
				se.name, se.stock_entry_type, len(se.get("items")),
				"" if len(se.get("items")) == 1 else "s",
			),
			title=_("Parchment Received"), indicator="green",
		)

	# ── Batch allocation ──────────────────────────────────────────────

	def _allocate_row_batches(self, row, parchment_item, parchment_is_batched):
		"""Return [(batch_no, qty), …] summing to `row.qty_kg` for this
		intake row.
		  - Non-batched item → single (None, qty).
		  - Internal → FIFO from source_warehouse's existing batches
		    (reuses _allocate_batches from outturn_statement).
		  - External → one fresh batch, minted here."""
		qty = flt(row.qty_kg)
		if not parchment_is_batched:
			return [(None, qty)]

		if self.is_internal:
			# Reuse the FIFO allocator inlined in outturn_statement.
			from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import (
				_allocate_batches,
			)
			return _allocate_batches(parchment_item, row.source_warehouse, qty)

		# External / Material Receipt — mint a fresh batch keyed by the
		# outturn number + parchment type so downstream reports can
		# trace stock back to the intake.
		batch_no = self._ensure_intake_batch(parchment_item, row.parchment_type)
		return [(batch_no, qty)]

	def _ensure_intake_batch(self, parchment_item, parchment_type):
		"""Create a Batch record for this intake row if one doesn't already
		exist. Name pattern: `{outturn_number}-{parchment_type}`; falls back
		to `{intake_name}-{parchment_type}` when there's no outturn number
		yet (shouldn't happen in the normal flow)."""
		key_prefix = (self.outturn_number or self.name or "COI").strip()
		suffix = (parchment_type or "PARCH").strip()
		batch_id = f"{key_prefix}-{suffix}"
		if frappe.db.exists("Batch", batch_id):
			return batch_id
		batch = frappe.get_doc({
			"doctype":   "Batch",
			"batch_id":  batch_id,
			"item":      parchment_item,
			"reference_doctype": self.doctype,
			"reference_name":    self.name,
		})
		batch.insert(ignore_permissions=True)
		return batch.name

	def _cancel_stock_entry(self):
		if not self.intake_stock_entry:
			return
		if not frappe.db.exists("Stock Entry", self.intake_stock_entry):
			return
		se = frappe.get_doc("Stock Entry", self.intake_stock_entry)
		if se.docstatus == 1:
			se.cancel()
		self.db_set("intake_stock_entry", None, update_modified=False)


# ─────────────────────────────────────────────────────────────────────────
# Client-facing helper: auto-populate items from the SO's parchment types.
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_intake_rows_from_sales_order(sales_order):
	"""Return the intake row template for a Coffee SO — one row per parchment
	type with the SO's expected weight + source_warehouse copied over."""
	if not sales_order or not frappe.db.exists("Sales Order", sales_order):
		return []
	so = frappe.get_doc("Sales Order", sales_order)
	rows = []
	for r in (so.get("custom_parchment_types") or []):
		rows.append({
			"parchment_type":   r.parchment_type,
			"qty_kg":           flt(r.expected_weight_kg),
			"source_warehouse": r.get("source_warehouse"),
		})
	return rows
