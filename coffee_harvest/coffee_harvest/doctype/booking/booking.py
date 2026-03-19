import frappe
from frappe import _
from frappe.model.document import Document


PARCHMENT_ITEM = "COFFEE-PARCHMENT"
DRY_MILL_WH = "Coffee Dry Mill - KL"
COMPANY = "Kaitet Ltd."


class Booking(Document):
    def validate(self):
        self._compute_net_weight()
        self._fetch_bin_stock()

    def before_submit(self):
        if self.status == "Transferred":
            self._validate_transfer()

    def _compute_net_weight(self):
        if self.no_of_bags and self.bag_weight:
            self.net_weight = self.no_of_bags * self.bag_weight
        elif self.no_of_bags:
            # Default 60 kg per bag
            self.net_weight = self.no_of_bags * 60.0

    def _fetch_bin_stock(self):
        if self.source_bin:
            self.current_bin_stock = (
                frappe.db.get_value(
                    "Bin",
                    {"warehouse": self.source_bin, "item_code": PARCHMENT_ITEM},
                    "actual_qty",
                )
                or 0
            )

    def _validate_transfer(self):
        if self.status == "Transferred":
            if not self.source_bin:
                frappe.throw(_("Source Bin is required when status is 'Transferred'."))
            if not self.net_weight or self.net_weight <= 0:
                frappe.throw(_("Net Weight is required when status is 'Transferred'."))
            if self.net_weight > (self.current_bin_stock or 0):
                frappe.throw(
                    _("Insufficient stock in {0}. Available: {1} kg, Required: {2} kg.").format(
                        self.source_bin, self.current_bin_stock, self.net_weight
                    )
                )

    def on_submit(self):
        pass  # handled by doc_events


def on_submit_transfer_to_mill(doc, method):
    """On submit: if status is Transferred, move parchment to dry mill."""
    if doc.status not in ("Transferred", "Milling", "Completed"):
        # Not yet transferred -- just booking
        doc.status = "Booked"
        frappe.db.set_value("Booking", doc.name, "status", "Booked", update_modified=False)
        return
    if doc.transfer_stock_entry:
        return  # Already done
    if not doc.source_bin or not doc.net_weight:
        frappe.throw(_("Source Bin and Net Weight are required to transfer to mill."))

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.posting_date = doc.transfer_date or frappe.utils.today()
    se.company = COMPANY
    se.remarks = f"Parchment transfer to dry mill for outturn {doc.outturn_number}"
    se.append(
        "items",
        {
            "item_code": PARCHMENT_ITEM,
            "qty": doc.net_weight,
            "uom": "Kilogram",
            "s_warehouse": doc.source_bin,
            "t_warehouse": DRY_MILL_WH,
        },
    )
    se.insert(ignore_permissions=True)
    se.submit()

    frappe.db.set_value(
        "Booking", doc.name, "transfer_stock_entry", se.name, update_modified=False
    )
    frappe.msgprint(
        _("Stock Entry {0} created: {1} kg parchment moved to {2}.").format(
            se.name, doc.net_weight, DRY_MILL_WH
        ),
        indicator="green",
    )


def on_cancel_reverse_transfer(doc, method):
    """Cancel the transfer stock entry on booking cancel."""
    if doc.transfer_stock_entry and frappe.db.exists("Stock Entry", doc.transfer_stock_entry):
        se = frappe.get_doc("Stock Entry", doc.transfer_stock_entry)
        if se.docstatus == 1:
            se.cancel()
    frappe.db.set_value(
        "Booking", doc.name, "transfer_stock_entry", None, update_modified=False
    )
