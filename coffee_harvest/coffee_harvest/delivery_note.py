import frappe


MILLED_STORE_WH = "Coffee Clean Warehouse - KL"


def calculate_item_weights(doc, method):
    """Calculate qty from bags (60 kg) + pockets for coffee dispatch items."""
    for row in doc.items:
        bags = row.get("custom_no_of_bags") or 0
        pockets = row.get("custom_no_of_pockets") or 0
        if bags or pockets:
            row.qty = bags * 60 + pockets

    # Auto-set batch_no for Endebess Coffee Dispatch items
    if doc.get("custom_delivery_type") == "Coffee Dispatch":
        for row in doc.items:
            outturn_ref = row.get("custom_outturn_number")
            if outturn_ref and row.item_code and not row.batch_no:
                batch_id = f"{outturn_ref}-{row.item_code}"
                if frappe.db.exists("Batch", batch_id):
                    row.batch_no = batch_id
