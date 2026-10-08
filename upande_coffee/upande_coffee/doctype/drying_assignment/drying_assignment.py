import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


CHERRY_ITEM = "Coffee-Cherry-Batched"
PARCHMENT_ITEM = "COFFEE-PARCHMENT"
WET_MILL_WH = "Coffee Wet Mill - KL"
COMPANY = "Kaitet Ltd."
# Fallback only — real capacity lives on Drying Table.capacity_debes so ops can
# set it per table without a code change.
DEFAULT_TABLE_CAPACITY_DEBES = 50


def _table_capacity(drying_table):
    cap = frappe.db.get_value("Drying Table", drying_table, "capacity_debes")
    return int(cap) if cap and int(cap) > 0 else DEFAULT_TABLE_CAPACITY_DEBES


def _debes_on_table(drying_table, exclude_assignment=None):
    """Debes physically sitting on a table right now.

    Derived from every live Drying Assignment row that names the table, NOT
    from Drying Table.current_debes — that field is a last-write-wins cache
    (each assignment overwrote it), so a table loaded by two batches reported
    only the most recent one and silently accepted well over its capacity.
    """
    return flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(dte.debes_quantity), 0)
            FROM `tabDrying Table Entry` dte
            JOIN `tabDrying Assignment` da ON da.name = dte.parent
            WHERE dte.drying_table = %(table)s
              AND dte.parenttype = 'Drying Assignment'
              AND da.docstatus < 2
              AND da.drying_status != 'Completed'
              AND da.name != %(exclude)s
            """,
            {"table": drying_table, "exclude": exclude_assignment or ""},
        )[0][0]
    )


class DryingAssignment(Document):
    def validate(self):
        self._validate_tables()
        self._calculate_totals()
        if self.drying_status == "Completed":
            self._validate_completion()

    def _validate_tables(self):
        if not self.table_assignments:
            frappe.throw(_("At least one drying table must be assigned."))

        # A table may legitimately appear on several rows (different coffee
        # types on the same table) and be shared with other live assignments.
        # Only the total across all of them is capped.
        wanted = {}
        for row in self.table_assignments:
            if not row.drying_table:
                frappe.throw(_("Row {0}: Drying Table is required.").format(row.idx))
            if flt(row.debes_quantity) < 0:
                frappe.throw(_("Row {0}: Debes cannot be negative.").format(row.idx))
            wanted.setdefault(row.drying_table, []).append(row)

        if self.drying_status == "Completed":
            # These rows are the original placement, already excluded from
            # every OTHER document's _debes_on_table() the moment drying_status
            # flips to Completed (see that function's own filter). Re-checking
            # capacity here would compare this doc's own (already-placed, never
            # changing) debes against whatever has since filled the table while
            # this assignment sat waiting to be submitted — a guaranteed false
            # "table full" the longer a Completed assignment goes un-submitted.
            return

        for drying_table, rows in wanted.items():
            capacity = _table_capacity(drying_table)
            already_on_table = _debes_on_table(drying_table, exclude_assignment=self.name)
            adding = sum(flt(r.debes_quantity) for r in rows)

            if already_on_table + adding > capacity:
                free = max(0, capacity - already_on_table)
                frappe.throw(
                    _(
                        "Drying Table {0} holds {1} of {2} debes — only {3} free, "
                        "but row {4} adds {5}."
                    ).format(
                        drying_table,
                        flt(already_on_table, 0),
                        capacity,
                        flt(free, 0),
                        ", ".join(str(r.idx) for r in rows),
                        flt(adding, 0),
                    )
                )

    def _validate_completion(self):
        if not self.removal_mode:
            frappe.throw(_("Please select a Removal Mode on the 'Remove Batch' tab before marking as Completed."))
        if self.removal_mode == "Full Batch":
            if self.full_batch_bin_entries:
                # Multi-bin mode: validate each row
                for row in self.full_batch_bin_entries:
                    if not row.weight_kg or row.weight_kg <= 0:
                        frappe.throw(_("Bin Destinations row {0}: Weight (kg) is required.").format(row.idx))
                    if not row.target_bin:
                        frappe.throw(_("Bin Destinations row {0}: Target Bin is required.").format(row.idx))
            else:
                # Single-bin mode
                if not self.full_batch_final_weight or self.full_batch_final_weight <= 0:
                    frappe.throw(_("Final Weight is required for Full Batch removal."))
                if not self.full_batch_target_bin:
                    frappe.throw(_("Target Bin is required for Full Batch removal."))
        elif self.removal_mode == "Per Table":
            if not self.table_removals:
                frappe.throw(_("Please add at least one row in Per-Table Removal."))
            for row in self.table_removals:
                if not row.final_weight_kg or row.final_weight_kg <= 0:
                    frappe.throw(
                        _("Row {0}: Final Weight is required.").format(row.idx)
                    )
                if not row.target_bin:
                    frappe.throw(
                        _("Row {0}: Target Bin is required.").format(row.idx)
                    )
        elif self.removal_mode == "Per Coffee Type":
            if not self.type_removals:
                frappe.throw(_("Please add at least one row in Per-Coffee-Type Removal."))
            for row in self.type_removals:
                if not row.coffee_type:
                    frappe.throw(_("Row {0}: Coffee Type is required.").format(row.idx))
                if not row.final_weight_kg or row.final_weight_kg <= 0:
                    frappe.throw(_("Row {0}: Final Weight is required.").format(row.idx))
                if not row.target_bin:
                    frappe.throw(_("Row {0}: Target Bin is required.").format(row.idx))

    def _calculate_totals(self):
        self.total_debes = sum(row.debes_quantity or 0 for row in self.table_assignments)
        self.total_initial_weight_kg = sum(row.initial_weight_kg or 0 for row in self.table_assignments)

        if self.removal_mode == "Full Batch":
            if self.full_batch_bin_entries:
                self.total_final_weight_kg = sum(row.weight_kg or 0 for row in self.full_batch_bin_entries)
            else:
                self.total_final_weight_kg = self.full_batch_final_weight or 0
        elif self.removal_mode == "Per Table":
            self.total_final_weight_kg = sum(row.final_weight_kg or 0 for row in (self.table_removals or []))
        elif self.removal_mode == "Per Coffee Type":
            self.total_final_weight_kg = sum(row.final_weight_kg or 0 for row in (self.type_removals or []))
        else:
            self.total_final_weight_kg = 0

        if self.total_initial_weight_kg > 0:
            self.yield_percentage = (self.total_final_weight_kg / self.total_initial_weight_kg) * 100
            self.milling_loss_kg = self.total_initial_weight_kg - self.total_final_weight_kg
        if self.batch:
            batch_qty = frappe.db.get_value("Batch", self.batch, "batch_qty") or 0
            self.batch_qty = batch_qty

    def on_update(self):
        # NOTE: was named `on_save`, which Frappe never calls (the real hook
        # is `on_update`) — table occupancy silently never updated on either
        # draft save or submit.
        self._update_drying_table_status()

    def _update_drying_table_status(self):
        for drying_table in {r.drying_table for r in self.table_assignments if r.drying_table}:
            _refresh_drying_table(drying_table)

    def before_submit(self):
        if self.drying_status != "Completed":
            frappe.throw(_("Cannot submit: Drying status must be 'Completed' before submitting."))
        if not self.completed_drying:
            frappe.throw(_("Please check 'Drying Completed' before submitting."))
        self._validate_completion()

    def on_submit(self):
        pass  # handled by doc_events hook

    def on_cancel(self):
        self._update_drying_table_status()


def _refresh_drying_table(drying_table):
    """Recompute a table's occupancy from the live assignments that name it.

    Called after any assignment save/submit/cancel. Because it re-derives
    rather than overwrites, a table shared by two batches stays Occupied with
    the correct running total when only one of them is completed.
    """
    if not frappe.db.exists("Drying Table", drying_table):
        return

    rows = frappe.db.sql(
        """
        SELECT da.batch, da.start_date, dte.coffee_type, dte.debes_quantity
        FROM `tabDrying Table Entry` dte
        JOIN `tabDrying Assignment` da ON da.name = dte.parent
        WHERE dte.drying_table = %s
          AND dte.parenttype = 'Drying Assignment'
          AND da.docstatus < 2
          AND da.drying_status != 'Completed'
        ORDER BY da.start_date ASC
        """,
        drying_table,
        as_dict=True,
    )

    total = sum(flt(r.debes_quantity) for r in rows)
    if not rows or total <= 0:
        frappe.db.set_value(
            "Drying Table",
            drying_table,
            {
                "status": "Available",
                "current_batch": None,
                "current_coffee_type": None,
                "current_debes": 0,
                "date_loaded": None,
            },
            update_modified=False,
        )
        return

    batches = sorted({r.batch for r in rows if r.batch})
    types = sorted({r.coffee_type for r in rows if r.coffee_type})
    frappe.db.set_value(
        "Drying Table",
        drying_table,
        {
            "status": "Occupied",
            # Several batches can share a table; the Link field can only name
            # one, so it shows the oldest and current_debes carries the total.
            "current_batch": rows[0].batch,
            "current_coffee_type": types[0] if len(types) == 1 else "",
            "current_debes": int(total),
            "date_loaded": rows[0].start_date,
        },
        update_modified=False,
    )
    if len(batches) > 1:
        frappe.msgprint(
            _("Drying Table {0} now holds {1} debes from {2} batches.").format(
                drying_table, int(total), len(batches)
            ),
            indicator="blue",
            alert=True,
        )


def on_submit_create_repack(doc, method):
    """Create Repack stock entry: Cherry -> Parchment split by target bins."""
    if doc.repack_created:
        frappe.msgprint(_("Repack entry already created for this drying assignment."))
        return

    # Get batch available stock
    batch_stock = _get_batch_stock(doc.batch, WET_MILL_WH)
    if batch_stock <= 0:
        frappe.throw(
            _("No stock available for batch {0} in {1}. Cannot create repack entry.").format(
                doc.batch, WET_MILL_WH
            )
        )

    # Build bin_weights from removal mode
    bin_weights = {}
    if doc.removal_mode == "Full Batch":
        if doc.full_batch_bin_entries:
            # Multi-bin entries take precedence
            for row in doc.full_batch_bin_entries:
                if row.weight_kg and row.weight_kg > 0 and row.target_bin:
                    bin_weights[row.target_bin] = bin_weights.get(row.target_bin, 0) + row.weight_kg
        elif doc.full_batch_target_bin and doc.full_batch_final_weight:
            bin_weights[doc.full_batch_target_bin] = doc.full_batch_final_weight
    elif doc.removal_mode == "Per Table":
        for row in doc.table_removals:
            if row.final_weight_kg and row.final_weight_kg > 0 and row.target_bin:
                bin_weights[row.target_bin] = bin_weights.get(row.target_bin, 0) + row.final_weight_kg
    elif doc.removal_mode == "Per Coffee Type":
        for row in doc.type_removals:
            if row.final_weight_kg and row.final_weight_kg > 0 and row.target_bin:
                bin_weights[row.target_bin] = bin_weights.get(row.target_bin, 0) + row.final_weight_kg

    if not bin_weights:
        frappe.throw(_("No final weights or target bins found. Cannot create repack entry."))

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Repack"
    se.posting_date = doc.end_date or frappe.utils.today()
    # Same fix as Harvest Pickup's stock entry: without this Frappe overwrites
    # posting_date with the submit-time moment, silently misdating any repack
    # submitted after end_date (the normal case — drying finishes, someone
    # marks it Completed and submits a day or more later).
    se.set_posting_time = 1
    se.posting_time = "12:00:00"
    se.company = COMPANY
    se.remarks = f"Auto-created from Drying Assignment {doc.name}: Cherry -> Parchment"

    # Source item: cherry from wet mill
    se.append(
        "items",
        {
            "item_code": CHERRY_ITEM,
            "qty": batch_stock,
            "uom": "Kilogram",
            "s_warehouse": WET_MILL_WH,
            "batch_no": doc.batch,
            "use_serial_batch_fields": 1,
        },
    )

    # Finished goods: parchment split by target bin
    for target_bin, weight in bin_weights.items():
        se.append(
            "items",
            {
                "item_code": PARCHMENT_ITEM,
                "qty": weight,
                "uom": "Kilogram",
                "t_warehouse": target_bin,
                "is_finished_item": 1,
            },
        )

    try:
        se.insert(ignore_permissions=True)
        se.submit()
    except Exception as e:
        frappe.throw(_("Failed to create Repack Entry: {0}").format(str(e)))

    frappe.db.set_value(
        "Drying Assignment",
        doc.name,
        {"repack_created": 1, "linked_repack_entry": se.name},
        update_modified=False,
    )
    # Release this assignment's share of each table — anything another live
    # assignment still has on it stays counted.
    for drying_table in {r.drying_table for r in doc.table_assignments if r.drying_table}:
        _refresh_drying_table(drying_table)

    frappe.msgprint(
        _("Repack Entry {0} created successfully. {1} kg parchment produced.").format(
            se.name, doc.total_final_weight_kg
        ),
        indicator="green",
        title=_("Drying Complete"),
    )


def on_cancel_reverse_repack(doc, method):
    """Cancel the repack entry on drying assignment cancel."""
    if doc.linked_repack_entry and frappe.db.exists("Stock Entry", doc.linked_repack_entry):
        se = frappe.get_doc("Stock Entry", doc.linked_repack_entry)
        if se.docstatus == 1:
            se.cancel()
    frappe.db.set_value(
        "Drying Assignment",
        doc.name,
        {"repack_created": 0, "linked_repack_entry": None},
        update_modified=False,
    )
    # Restore table status
    for drying_table in {r.drying_table for r in doc.table_assignments if r.drying_table}:
        _refresh_drying_table(drying_table)


def _get_batch_stock(batch, warehouse):
    """Get available stock for a batch in a warehouse.

    Must go through ERPNext's own get_batch_qty, not a raw Stock Ledger Entry
    query: this site uses the Serial and Batch Bundle model (use_serial_batch_fields),
    where batch association lives in Serial and Batch Entry rows, and
    Stock Ledger Entry.batch_no is always NULL — a direct SLE.batch_no query
    silently returns 0 for every batch, however much stock is really there."""
    from erpnext.stock.doctype.batch.batch import get_batch_qty

    return flt(get_batch_qty(batch_no=batch, warehouse=warehouse))


@frappe.whitelist()
def get_available_tables(exclude_assignment=None):
    """Drying tables with room left, for quick assignment.

    A partly-loaded table is still usable, so this returns anything with free
    space — not just status == "Available", which hid every table that had so
    much as one debe on it and was why a table could never be reused.
    """
    tables = frappe.get_all(
        "Drying Table",
        filters={"status": ["!=", "Under Maintenance"]},
        fields=["name", "table_id", "status", "capacity_debes"],
        order_by="table_id asc",
    )
    out = []
    for t in tables:
        capacity = int(t.capacity_debes) if t.capacity_debes and int(t.capacity_debes) > 0 else DEFAULT_TABLE_CAPACITY_DEBES
        used = _debes_on_table(t.name, exclude_assignment=exclude_assignment)
        free = capacity - used
        if free <= 0:
            continue
        out.append({
            "name": t.name,
            "table_id": t.table_id,
            "status": t.status,
            "capacity_debes": capacity,
            "used_debes": int(used),
            "free_debes": int(free),
        })
    return out


@frappe.whitelist()
def get_tables_by_batch(batch):
    """Return all active drying assignments for a given batch."""
    assignments = frappe.db.sql(
        """
        SELECT da.name, da.start_date, da.drying_status, dte.drying_table, dte.coffee_type,
               dte.debes_quantity, dte.initial_weight_kg
        FROM `tabDrying Assignment` da
        JOIN `tabDrying Table Entry` dte ON dte.parent = da.name
        WHERE da.batch = %s AND da.docstatus < 2
        """,
        batch,
        as_dict=True,
    )
    return assignments
