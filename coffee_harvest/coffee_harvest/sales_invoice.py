import frappe


def copy_farm_from_delivery_note(doc, method):
    """Copy custom_farm and custom_business_unit from the source Delivery Note.

    When a Sales Invoice is made from a Delivery Note, ERPNext does not
    automatically carry over custom fields.  These two fields are mandatory
    on Sales Invoice (upande_kaitet), so we pull them from the first linked DN.
    """
    if doc.custom_farm and doc.custom_business_unit:
        return  # already populated — nothing to do

    # Find the first Delivery Note referenced in the items table
    dn_name = None
    for item in doc.items:
        if item.get("delivery_note"):
            dn_name = item.delivery_note
            break

    if not dn_name:
        return

    farm, bu = frappe.db.get_value(
        "Delivery Note", dn_name, ["custom_farm", "custom_business_unit"]
    ) or (None, None)

    if farm and not doc.custom_farm:
        doc.custom_farm = farm
    if bu and not doc.custom_business_unit:
        doc.custom_business_unit = bu
