import frappe
from frappe import _
from frappe.model.document import Document


CHERRY_ITEM = "Coffee-Cherry-Batched"
PARCHMENT_ITEM = "COFFEE-PARCHMENT"
WET_MILL_WH = "Coffee Wet Mill - KL"
COMPANY = "Kaitet Ltd."


class DryingAssignment(Document):
    def validate(self):
        self._validate_tables()
        self._calculate_totals()
        if self.drying_status == "Completed":
            self._validate_completion()

    def _validate_tables(self):
        if not self.table_assignments:
            frappe.throw(_("At least one drying table must be assigned."))
        tables_used = []
        for row in self.table_assignments:
            if not row.drying_table:
                frappe.throw(_("Row {0}: Drying Table is required.").format(row.idx))
            if row.drying_table in tables_used:
                frappe.throw(_("Row {0}: Drying Table {1} is used more than once.").format(row.idx, row.drying_table))
            tables_used.append(row.drying_table)

    def _validate_completion(self):
        if not self.removal_mode:
            frappe.throw(_("Please select a Removal Mode on the 'Remove Batch' tab before marking as Completed."))
        if self.removal_mode == "Full Batch":
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

    def on_save(self):
        self._update_drying_table_status()

    def _update_drying_table_status(self):
        for row in self.table_assignments:
            if frappe.db.exists("Drying Table", row.drying_table):
                if self.drying_status == "Completed" or self.docstatus == 2:
                    frappe.db.set_value(
                        "Drying Table",
                        row.drying_table,
                        {
                            "status": "Available",
                            "current_batch": None,
                            "current_coffee_type": None,
                            "current_debes": 0,
                            "date_loaded": None,
                        },
                        update_modified=False,
                    )
                else:
                    frappe.db.set_value(
                        "Drying Table",
                        row.drying_table,
                        {
                            "status": "Occupied",
                            "current_batch": self.batch,
                            "current_coffee_type": row.coffee_type or "",
                            "current_debes": row.debes_quantity or 0,
                            "date_loaded": self.start_date,
                        },
                        update_modified=False,
                    )

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
        if doc.full_batch_target_bin and doc.full_batch_final_weight:
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
    # Free up the drying tables
    for row in doc.table_assignments:
        if frappe.db.exists("Drying Table", row.drying_table):
            frappe.db.set_value(
                "Drying Table",
                row.drying_table,
                {"status": "Available", "current_batch": None, "current_coffee_type": None, "current_debes": 0, "date_loaded": None},
                update_modified=False,
            )

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
    for row in doc.table_assignments:
        if frappe.db.exists("Drying Table", row.drying_table):
            frappe.db.set_value(
                "Drying Table",
                row.drying_table,
                {"status": "Available", "current_batch": None, "current_coffee_type": None, "current_debes": 0, "date_loaded": None},
                update_modified=False,
            )


def _get_batch_stock(batch, warehouse):
    """Get available stock for a batch in a warehouse."""
    result = frappe.db.sql(
        """
        SELECT SUM(actual_qty)
        FROM `tabStock Ledger Entry`
        WHERE batch_no = %s AND warehouse = %s AND is_cancelled = 0
        """,
        (batch, warehouse),
    )
    return result[0][0] or 0 if result else 0


@frappe.whitelist()
def get_available_tables():
    """Return list of available drying tables for quick assignment."""
    tables = frappe.get_all(
        "Drying Table",
        filters={"status": "Available"},
        fields=["name", "table_id", "status"],
        order_by="table_id asc",
    )
    return tables


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
