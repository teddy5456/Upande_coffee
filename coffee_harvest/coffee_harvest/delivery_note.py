import frappe


MILLED_STORE_WH = "Coffee Clean Warehouse - KL"


def calculate_item_weights(doc, method):
    """Calculate qty from bags (60 kg) + pockets for coffee dispatch items.
    Also corrects warehouse and batch_no for Coffee Dispatch DNs.
    """
    is_coffee_dispatch = doc.get("custom_delivery_type") == "Coffee Dispatch"

    # Override set_warehouse — the Delivery Type fetch_from may pull the wrong warehouse
    if is_coffee_dispatch:
        doc.set_warehouse = MILLED_STORE_WH

    for row in doc.items:
        # Qty from bags + pockets
        bags = row.get("custom_no_of_bags") or 0
        pockets = row.get("custom_no_of_pockets") or 0
        if bags or pockets:
            row.qty = bags * 60 + pockets

        if is_coffee_dispatch:
            # Ensure each stock item uses the correct source warehouse and UOM
            if frappe.db.get_value("Item", row.item_code, "is_stock_item"):
                row.warehouse = MILLED_STORE_WH
                row.uom = "Kilogram"

            # Auto-set batch_no from outturn + item_code
            outturn_ref = row.get("custom_outturn_number")
            if outturn_ref and row.item_code and not row.batch_no:
                batch_id = f"{outturn_ref}-{row.item_code}"
                if frappe.db.exists("Batch", batch_id):
                    row.batch_no = batch_id


def fix_coffee_dispatch_warehouse(doc, method):
    """Force the correct source warehouse on Coffee Dispatch DNs.

    The upande_kaitet app has a Property Setter:
        set_warehouse → fetch_from → custom_delivery_type.source_warehouse
    This runs server-side AFTER before_validate, so it resets set_warehouse to
    whatever the Delivery Type record has (Yogurt Coldroom - KR).
    This before_save hook runs last and corrects it.
    """
    if doc.get("custom_delivery_type") != "Coffee Dispatch":
        return

    doc.set_warehouse = MILLED_STORE_WH
    for row in doc.items:
        if not frappe.db.get_value("Item", row.item_code, "is_stock_item"):
            row.warehouse = ""
        else:
            row.warehouse = MILLED_STORE_WH


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_grower_customers(doctype, txt, searchfield, start, page_len, filters):
    """Search only customers who have at least one Booking (i.e. are growers)."""
    return frappe.db.sql(
        """
        SELECT DISTINCT c.name, c.customer_name
        FROM `tabCustomer` c
        JOIN `tabBooking` b ON b.grower = c.name
        WHERE (c.name LIKE %(txt)s OR c.customer_name LIKE %(txt)s)
        ORDER BY c.name
        LIMIT %(page_len)s OFFSET %(start)s
        """,
        {"txt": f"%{txt}%", "page_len": int(page_len), "start": int(start)},
    )


@frappe.whitelist()
def get_available_outturn_grades(outturn_name):
    """Return grade rows for a submitted Outturn Statement so the UI can pre-fill items."""
    if not outturn_name or not frappe.db.exists("Outturn Statement", outturn_name):
        return []
    doc = frappe.get_doc("Outturn Statement", outturn_name)
    if doc.docstatus != 1:
        return []
    result = []
    for row in doc.table_cyvh:
        batch_id = f"{outturn_name}-{row.grade}"
        result.append(
            {
                "grade": row.grade,
                "item_code": row.item_code or row.grade,
                "no_of_bags": row.no_of_bags or 0,
                "no_of_pockets": row.no_of_pockets or 0,
                "net_weight": row.net_weight or 0,
                "batch_id": batch_id if frappe.db.exists("Batch", batch_id) else None,
            }
        )
    return result
