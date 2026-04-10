"""Create and maintain Coffee Harvest custom fields on standard ERPNext doctypes."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


COFFEE_DISPATCH = "Coffee Dispatch"

# Fields are only visible when the Delivery Note is a Coffee Dispatch.
_DEPENDS_ON_ITEM = f"eval:parent.custom_delivery_type=='{COFFEE_DISPATCH}'"
_DEPENDS_ON_HDR = f"eval:doc.custom_delivery_type=='{COFFEE_DISPATCH}'"


def create_coffee_custom_fields():
    """Idempotently create Coffee Harvest custom fields.
    Called on after_install and after_migrate.
    """
    create_custom_fields(
        {
            # ── Delivery Note header ──────────────────────────────────────────
            "Delivery Note": [
                {
                    "fieldname": "custom_outturn_references",
                    "fieldtype": "Link",
                    "label": "Outturn Statement",
                    "options": "Outturn Statement",
                    "insert_after": "custom_delivery_type",
                    "depends_on": _DEPENDS_ON_HDR,
                    "read_only": 0,
                    "in_list_view": 0,
                    "module": "Coffee Harvest",
                },
            ],
            # ── Delivery Note Item (line-level coffee fields) ─────────────────
            "Delivery Note Item": [
                {
                    "fieldname": "custom_coffee_sec",
                    "fieldtype": "Section Break",
                    "label": "Coffee Dispatch",
                    "insert_after": "batch_no",
                    "depends_on": _DEPENDS_ON_ITEM,
                    "collapsible": 0,
                    "module": "Coffee Harvest",
                },
                {
                    "fieldname": "custom_outturn_number",
                    "fieldtype": "Link",
                    "label": "Outturn Statement",
                    "options": "Outturn Statement",
                    "insert_after": "custom_coffee_sec",
                    "depends_on": _DEPENDS_ON_ITEM,
                    "in_list_view": 1,
                    "module": "Coffee Harvest",
                },
                {
                    "fieldname": "custom_grower",
                    "fieldtype": "Link",
                    "label": "Grower",
                    "options": "Customer",
                    "insert_after": "custom_outturn_number",
                    "depends_on": _DEPENDS_ON_ITEM,
                    "in_list_view": 1,
                    "module": "Coffee Harvest",
                },
                {
                    "fieldname": "custom_col_break_coffee",
                    "fieldtype": "Column Break",
                    "insert_after": "custom_grower",
                    "depends_on": _DEPENDS_ON_ITEM,
                    "module": "Coffee Harvest",
                },
                {
                    "fieldname": "custom_no_of_bags",
                    "fieldtype": "Int",
                    "label": "Bags (60 kg)",
                    "insert_after": "custom_col_break_coffee",
                    "depends_on": _DEPENDS_ON_ITEM,
                    "in_list_view": 1,
                    "module": "Coffee Harvest",
                },
                {
                    "fieldname": "custom_no_of_pockets",
                    "fieldtype": "Int",
                    "label": "Pockets (kg)",
                    "insert_after": "custom_no_of_bags",
                    "depends_on": _DEPENDS_ON_ITEM,
                    "in_list_view": 1,
                    "module": "Coffee Harvest",
                },
            ],
        },
        ignore_validate=True,
    )
    frappe.db.commit()
    _fix_coffee_grade_item_uom()
    _fix_coffee_service_item_uom()


# Grade item codes mirrored from outturn_statement.GRADE_ITEM_MAP
_GRADE_ITEMS = ["AA", "AB", "PB", "C", "TT", "T", "HE", "E", "NH", "NL", "ML", "MH", "SB", "UG1", "UG2", "UG3"]

# Service charge items billed on outgrower Delivery Notes
_SERVICE_ITEMS = ["COFFEE-MILLING", "COFFEE-HANDLING", "COFFEE-EXPORT-BAGS", "COFFEE-TRANSPORT"]


def _fix_coffee_grade_item_uom():
    """Ensure coffee grade items use Kilogram as their stock UOM."""
    for item_code in _GRADE_ITEMS:
        if not frappe.db.exists("Item", item_code):
            continue
        if frappe.db.get_value("Item", item_code, "stock_uom") == "Kilogram":
            continue
        frappe.db.set_value("Item", item_code, "stock_uom", "Kilogram", update_modified=False)
        has_kg = frappe.db.exists("UOM Conversion Detail", {"parent": item_code, "uom": "Kilogram"})
        if not has_kg:
            item = frappe.get_doc("Item", item_code)
            item.append("uoms", {"uom": "Kilogram", "conversion_factor": 1})
            item.save(ignore_permissions=True)
    frappe.db.commit()


def _fix_coffee_service_item_uom():
    """Ensure service charge items use a UOM that allows decimal quantities.

    Outgrower invoices price charges per tonne of output (e.g. qty=0.92),
    so 'Nos' (Must be Whole Number) must not be the stock UOM.
    """
    # Ensure the UOMs exist and allow decimal quantities
    for uom_name in ("Tonne", "Bags"):
        if not frappe.db.exists("UOM", uom_name):
            frappe.get_doc({"doctype": "UOM", "uom_name": uom_name, "must_be_whole_number": 0}).insert(
                ignore_permissions=True
            )
        else:
            frappe.db.set_value("UOM", uom_name, "must_be_whole_number", 0, update_modified=False)

    # Item-level UOM mapping: each service item uses a specific UOM
    _item_uom = {
        "COFFEE-MILLING": "Tonne",
        "COFFEE-HANDLING": "Bags",
        "COFFEE-EXPORT-BAGS": "Bags",
        "COFFEE-TRANSPORT": "Bags",
    }
    for item_code, target_uom in _item_uom.items():
        if not frappe.db.exists("Item", item_code):
            continue
        if frappe.db.get_value("Item", item_code, "stock_uom") == target_uom:
            continue
        frappe.db.set_value("Item", item_code, "stock_uom", target_uom, update_modified=False)
        has_uom = frappe.db.exists("UOM Conversion Detail", {"parent": item_code, "uom": target_uom})
        if not has_uom:
            item = frappe.get_doc("Item", item_code)
            item.append("uoms", {"uom": target_uom, "conversion_factor": 1})
            item.save(ignore_permissions=True)
    frappe.db.commit()
