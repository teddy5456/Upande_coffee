import frappe
from frappe import _
from frappe.model.document import Document


CHERRY_ITEM = "Coffee-Cherry-Batched"
WET_MILL_WH = "Coffee Wet Mill - KL"
COMPANY = "Kaitet Ltd."


class HarvestPickup(Document):
    def validate(self):
        self._validate_weighbridge()
        self._calculate_totals()

    def _validate_weighbridge(self):
        if self.status == "Weighed":
            for row in self.block_pickups:
                if not row.weight_kg or row.weight_kg <= 0:
                    frappe.throw(
                        _("Row {0}: Weight (kg) is required when status is Weighed.").format(row.idx)
                    )
            if not self.total_weight_kg or self.total_weight_kg <= 0:
                frappe.throw(_("Total weight must be greater than 0 when status is Weighed."))

    def _calculate_totals(self):
        total_buckets = sum(row.bucket_count or 0 for row in self.block_pickups)
        total_weight = sum(row.weight_kg or 0 for row in self.block_pickups)
        self.total_buckets = total_buckets
        self.total_weight_kg = total_weight

    def on_submit(self):
        # Called via doc_events in hooks.py
        pass

    def on_cancel(self):
        pass


def on_submit_create_stock_entry(doc, method):
    """Create stock entry moving cherry from blocks to wet mill on pickup submit."""
    if doc.moved_stock:
        return
    if doc.status != "Weighed":
        frappe.throw(_("Cannot submit: status must be 'Weighed' before submitting."))
    if not doc.total_weight_kg or doc.total_weight_kg <= 0:
        frappe.throw(_("Cannot submit: total weight must be greater than 0."))

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
        batch.item = CHERRY_ITEM
        batch.insert(ignore_permissions=True)

    # Create stock entry: Material Receipt to wet mill
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Receipt"
    se.posting_date = doc.date
    se.company = COMPANY
    se.remarks = f"Cherry received from harvest pickup {doc.name} on {doc.date}"
    se.append(
        "items",
        {
            "item_code": CHERRY_ITEM,
            "qty": doc.total_weight_kg,
            "uom": "Kilogram",
            "t_warehouse": WET_MILL_WH,
            "batch_no": batch_name,
            "use_serial_batch_fields": 1,
        },
    )
    se.insert(ignore_permissions=True)
    se.submit()

    frappe.db.set_value("Harvest Pickup", doc.name, "moved_stock", 1, update_modified=False)
    frappe.db.set_value("Harvest Pickup", doc.name, "stock_entry", se.name, update_modified=False)
    frappe.msgprint(
        _("Stock Entry {0} created: {1} kg cherry moved to {2}.").format(
            se.name, doc.total_weight_kg, WET_MILL_WH
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
    # Unmark harvest logs
    for row in doc.block_pickups:
        if row.picked_log_ids:
            log_ids = [lid.strip() for lid in row.picked_log_ids.split(",") if lid.strip()]
            for log_id in log_ids:
                if frappe.db.exists("Harvest Log", log_id):
                    frappe.db.set_value("Harvest Log", log_id, "picked_up", 0, update_modified=False)
