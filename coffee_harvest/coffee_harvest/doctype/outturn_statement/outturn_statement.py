import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


PARCHMENT_ITEM = "COFFEE-PARCHMENT"
DRY_MILL_WH = "Coffee Dry Mill - KL"
MILLED_STORE_WH = "Coffee Clean Warehouse - KL"
COMPANY = "Kaitet Ltd."

# Map grade codes to item codes
GRADE_ITEM_MAP = {
    "AA": "AA",
    "AB": "AB",
    "PB": "PB",
    "C": "C",
    "TT": "TT",
    "T": "T",
    "HE": "HE",
    "E": "E",
    "NH": "NH",
    "NL": "NL",
    "ML": "ML",
    "MH": "MH",
    "SB": "SB",
    "UG1": "UG1",
    "UG2": "UG2",
    "UG3": "UG3",
}


class OutturnStatement(Document):
    def validate(self):
        self._fetch_parchment_weight()
        self._calculate_grade_weights()
        self._calculate_milling_loss()
        self._map_grade_items()

    def _fetch_parchment_weight(self):
        if self.outturn_type == "Super Outturn":
            total = 0
            growers = []
            for row in (self.component_bookings or []):
                booking = frappe.get_value(
                    "Booking", row.booking_outturn, ["net_weight", "grower"], as_dict=True
                )
                if booking:
                    total += booking.net_weight or 0
                    if booking.grower and booking.grower not in growers:
                        growers.append(booking.grower)
            self.parchment_weight = total
            self.grower = ", ".join(growers) if growers else self.grower
        elif self.outturn_number:
            booking = frappe.get_value(
                "Booking", self.outturn_number, ["net_weight", "grower"], as_dict=True
            )
            if booking:
                self.parchment_weight = booking.net_weight or 0
                self.grower = booking.grower or self.grower

    def _calculate_grade_weights(self):
        for row in self.table_cyvh:
            row.net_weight = (row.no_of_bags or 0) * 60 + (row.no_of_pockets or 0)

    def _calculate_milling_loss(self):
        if self.parchment_weight and self.parchment_weight > 0 and self.output_weight is not None:
            self.milling_loss = ((self.parchment_weight - self.output_weight) / self.parchment_weight) * 100

    def _map_grade_items(self):
        for row in self.table_cyvh:
            row.item_code = GRADE_ITEM_MAP.get(row.grade, row.grade)

    def before_submit(self):
        if self.outturn_type == "Super Outturn":
            if not self.component_bookings:
                frappe.throw(_("At least one Component Booking is required for a Super Outturn."))
            # Check no booking is already used in another submitted outturn
            for row in self.component_bookings:
                existing = frappe.db.sql(
                    """
                    SELECT os.name FROM `tabOutturn Statement` os
                    JOIN `tabOutturn Component` oc ON oc.parent = os.name
                    WHERE oc.booking_outturn = %s AND os.docstatus = 1 AND os.name != %s
                    """,
                    (row.booking_outturn, self.name),
                )
                if existing:
                    frappe.throw(
                        _("Booking {0} is already in a submitted Outturn Statement ({1}).").format(
                            row.booking_outturn, existing[0][0]
                        )
                    )
        else:
            if not self.outturn_number:
                frappe.throw(_("Booking (Outturn Number) is required for a Normal outturn."))
            # Check this booking is not already in another submitted outturn
            existing = frappe.db.get_value(
                "Outturn Statement",
                {"outturn_number": self.outturn_number, "docstatus": 1, "name": ("!=", self.name)},
                "name",
            )
            if existing:
                frappe.throw(
                    _("Booking {0} is already used in Outturn Statement {1}.").format(
                        self.outturn_number, existing
                    )
                )

        if not self.table_cyvh:
            frappe.throw(_("At least one grade row is required in the Outturn Details."))
        total_grade_weight = sum(
            (row.no_of_bags or 0) * 60 + (row.no_of_pockets or 0) for row in self.table_cyvh
        )
        if abs(total_grade_weight - self.output_weight) > 1:
            frappe.throw(
                _("Grade breakdown total ({0} kg) does not match output weight ({1} kg). Please reconcile.").format(
                    total_grade_weight, self.output_weight
                )
            )

    def on_submit(self):
        pass  # handled by doc_events


def on_submit_create_milled_stock(doc, method):
    """Create Repack Stock Entry: Parchment -> Graded Coffee items."""
    dry_mill_stock = _get_warehouse_stock(PARCHMENT_ITEM, DRY_MILL_WH)
    if dry_mill_stock < doc.output_weight:
        frappe.throw(
            _("Insufficient parchment in {0}. Available: {1} kg, Required: {2} kg.").format(
                DRY_MILL_WH, dry_mill_stock, doc.output_weight
            )
        )

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Repack"
    se.posting_date = frappe.utils.today()
    se.company = COMPANY
    # Use doc.name as the unique reference in remarks so cancel can find it reliably
    se.remarks = f"Milling output for {doc.name}"

    # Source: parchment from dry mill
    se.append(
        "items",
        {
            "item_code": PARCHMENT_ITEM,
            "qty": doc.parchment_weight or doc.output_weight,
            "uom": "Kilogram",
            "s_warehouse": DRY_MILL_WH,
        },
    )

    # Finished goods: each grade goes to milled store
    for row in doc.table_cyvh:
        weight = (row.no_of_bags or 0) * 60 + (row.no_of_pockets or 0)
        if weight > 0:
            item_code = GRADE_ITEM_MAP.get(row.grade, row.grade)
            if not frappe.db.exists("Item", item_code):
                frappe.throw(
                    _("Item {0} (for grade {1}) does not exist. Please create it first.").format(
                        item_code, row.grade
                    )
                )
            # Batch uses doc.name so Endebess can dispatch by outturn+grade
            batch_id = f"{doc.name}-{row.grade}"
            if not frappe.db.exists("Batch", batch_id):
                b = frappe.new_doc("Batch")
                b.batch_id = batch_id
                b.item = item_code
                b.insert(ignore_permissions=True)

            se.append(
                "items",
                {
                    "item_code": item_code,
                    "qty": weight,
                    "uom": "Kilogram",
                    "t_warehouse": MILLED_STORE_WH,
                    "is_finished_item": 1,
                    "batch_no": batch_id,
                    "use_serial_batch_fields": 1,
                },
            )

    se.insert(ignore_permissions=True)
    se.submit()

    # Update booking status
    if doc.outturn_type == "Super Outturn":
        for row in (doc.component_bookings or []):
            if frappe.db.exists("Booking", row.booking_outturn):
                frappe.db.set_value(
                    "Booking", row.booking_outturn, "status", "Completed", update_modified=False
                )
    elif doc.outturn_number and frappe.db.exists("Booking", doc.outturn_number):
        frappe.db.set_value(
            "Booking", doc.outturn_number, "status", "Completed", update_modified=False
        )

    frappe.msgprint(
        _("Milled stock entry {0} created. {1} kg graded coffee moved to {2}.").format(
            se.name, doc.output_weight, MILLED_STORE_WH
        ),
        indicator="green",
        title=_("Milling Complete"),
    )


def on_cancel_reverse_milled_stock(doc, method):
    """On cancel: find and cancel the linked milling stock entry."""
    # Try new-style remarks first (uses doc.name)
    se_name = frappe.db.get_value(
        "Stock Entry",
        {"remarks": f"Milling output for {doc.name}", "docstatus": 1},
        "name",
    )
    if not se_name:
        # Fallback for records created before the naming_series change
        se_name = frappe.db.get_value(
            "Stock Entry",
            {
                "remarks": f"Milling output for outturn {doc.outturn_number} - {doc.grower}",
                "docstatus": 1,
            },
            "name",
        )
    if se_name:
        se = frappe.get_doc("Stock Entry", se_name)
        se.cancel()

    # Reset booking statuses
    if doc.outturn_type == "Super Outturn":
        for row in (doc.component_bookings or []):
            if frappe.db.exists("Booking", row.booking_outturn):
                frappe.db.set_value(
                    "Booking", row.booking_outturn, "status", "Transferred", update_modified=False
                )
    elif doc.outturn_number and frappe.db.exists("Booking", doc.outturn_number):
        frappe.db.set_value(
            "Booking", doc.outturn_number, "status", "Transferred", update_modified=False
        )


@frappe.whitelist()
def create_delivery_note(outturn_name):
    """Create a draft Delivery Note from a submitted Outturn Statement.

    Outgrowers: service charges only (no stock movement).
    Endebess (internal): Coffee Dispatch header — user adds grade items manually.
    """
    doc = frappe.get_doc("Outturn Statement", outturn_name)
    if doc.linked_delivery_note:
        frappe.throw(_("Delivery Note already created: {0}").format(doc.linked_delivery_note))
    if doc.docstatus != 1:
        frappe.throw(_("Outturn Statement must be submitted before creating a Delivery Note."))

    # Determine customer and is_internal from booking
    if doc.outturn_type == "Super Outturn":
        # For super outturn use the first component booking to get customer
        first = doc.component_bookings[0] if doc.component_bookings else None
        if not first:
            frappe.throw(_("No component bookings found on this Super Outturn."))
        booking = frappe.get_value(
            "Booking", first.booking_outturn, ["grower", "is_internal"], as_dict=True
        )
    else:
        booking = frappe.get_value(
            "Booking", doc.outturn_number, ["grower", "is_internal"], as_dict=True
        )

    if not booking or not booking.grower:
        frappe.throw(_("No grower linked on the Booking for this outturn."))

    is_internal = cint(booking.is_internal)

    _ensure_usd_party_account(booking.grower)

    dn = frappe.new_doc("Delivery Note")
    dn.customer = booking.grower
    dn.company = COMPANY
    dn.currency = "USD"
    dn.posting_date = frappe.utils.today()
    dn.custom_outturn_references = doc.outturn_number or doc.name

    if is_internal:
        # Endebess: Coffee Dispatch header — items added manually via the DN form
        dn.custom_farm = "Endebess"
        dn.custom_business_unit = "Endebess Coffee"
        dn.custom_delivery_type = "Coffee Dispatch"
        dn.custom_location = "Endebess"
        dn.set_warehouse = MILLED_STORE_WH
    else:
        # Outgrower: service charges only — no stock items, no warehouse
        _add_outgrower_charge_items(dn, doc)

    dn.insert(ignore_permissions=True)
    frappe.db.set_value(
        "Outturn Statement", outturn_name, "linked_delivery_note", dn.name, update_modified=False
    )
    return dn.name


def _add_outgrower_charge_items(dn, outturn_doc):
    """Append service charge items to an outgrower Delivery Note."""
    charges = [
        ("COFFEE-MILLING", "Milling Charges"),
        ("COFFEE-HANDLING", "Handling Charges"),
        ("COFFEE-EXPORT-BAGS", "Export Bags"),
    ]
    if outturn_doc.transport_expenses:
        charges.append(("COFFEE-TRANSPORT", "Transport"))

    outturn_ref = outturn_doc.outturn_number or outturn_doc.name

    for item_code, label in charges:
        dn.append(
            "items",
            {
                "item_code": item_code,
                "item_name": label,
                "qty": 1,
                "uom": "Unit",
                "rate": 0,
                "description": f"{label} — outturn {outturn_ref} ({outturn_doc.grower})",
            },
        )


def _ensure_usd_party_account(customer):
    """Ensure the customer has a USD receivable account for Kaitet Ltd."""
    USD_DEBTORS = "101002010401 - Trade Debtors USD - KL"
    exists = frappe.db.exists(
        "Party Account", {"parent": customer, "company": COMPANY, "account": USD_DEBTORS}
    )
    if not exists:
        frappe.db.sql(
            """INSERT INTO `tabParty Account`
               (name, creation, modified, modified_by, owner, docstatus, idx, company, account, parent, parentfield, parenttype)
               VALUES (%s, NOW(), NOW(), 'Administrator', 'Administrator', 0, 1, %s, %s, %s, 'accounts', 'Customer')""",
            (frappe.generate_hash(length=10), COMPANY, USD_DEBTORS, customer),
        )
        frappe.db.commit()


def _get_warehouse_stock(item_code, warehouse):
    result = frappe.db.sql(
        """
        SELECT SUM(actual_qty)
        FROM `tabStock Ledger Entry`
        WHERE item_code = %s AND warehouse = %s AND is_cancelled = 0
        """,
        (item_code, warehouse),
    )
    return result[0][0] or 0 if result else 0
