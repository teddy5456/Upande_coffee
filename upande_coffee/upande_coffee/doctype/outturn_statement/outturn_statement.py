import frappe
from frappe.utils import flt
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


def _settings():
    s = frappe.get_cached_doc("Coffee Settings")
    if not (s.parchment_item and s.dry_mill_warehouse and s.milled_store_warehouse):
        frappe.throw(
            _("Set Parchment Item, Dry Mill Warehouse and Milled Store Warehouse in Coffee Settings.")
        )
    return s


def _bag_kg():
    return frappe.get_cached_doc("Coffee Settings").bag_weight_kg or 60


def _booking_exists():
    """The Booking doctype has been retired in favour of Sales Order. Many
    legacy code paths in this file still reference it — they're only
    executed if the doctype is still present in the DB (transitional
    installs). Sites on the current version return False here and the
    Booking-related work is silently skipped."""
    try:
        return bool(frappe.db.exists("DocType", "Booking"))
    except Exception:
        return False


# ── Helpers previously in booking.py — inlined here so the file survives the
#    Booking doctype removal. Same semantics.
def parchment_item_for(parchment_type=None):
    """Item a parchment type is stored as: Parchment Type.item, else an item
    named like the type, else the Coffee Settings default."""
    if parchment_type:
        item = frappe.db.get_value("Parchment Type", parchment_type, "item")
        if item:
            return item
        if frappe.db.exists("Item", parchment_type):
            return parchment_type
    return frappe.get_cached_doc("Coffee Settings").parchment_item


def _allocate_batches(item_code, warehouse, required_qty, prefer_batch=None):
    """FIFO batch allocation for a batched item in one warehouse, optionally
    consuming prefer_batch first. Returns [(batch_no, qty)] covering
    required_qty; for non-batched items a single unbatched row."""
    if not frappe.db.get_value("Item", item_code, "has_batch_no"):
        return [(None, flt(required_qty))]

    rows = frappe.db.sql(
        """SELECT COALESCE(sbe.batch_no, sle.batch_no) AS batch_no,
                SUM(COALESCE(sbe.qty, sle.actual_qty)) AS qty, MIN(sle.posting_date) first_seen
        FROM `tabStock Ledger Entry` sle
        LEFT JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
        WHERE sle.warehouse = %s AND sle.item_code = %s AND sle.is_cancelled = 0
        GROUP BY COALESCE(sbe.batch_no, sle.batch_no)
        HAVING batch_no IS NOT NULL AND qty > 0
        ORDER BY first_seen, batch_no""",
        (warehouse, item_code),
        as_dict=True,
    )
    if prefer_batch:
        rows.sort(key=lambda r: 0 if r.batch_no == prefer_batch else 1)
    remaining = flt(required_qty)
    alloc = []
    for r in rows:
        if remaining <= 0:
            break
        take = min(flt(r.qty), remaining)
        alloc.append((r.batch_no, take))
        remaining -= take
    if remaining > 0.01:
        frappe.throw(
            _("Insufficient batched stock of {0} in {1}: need {2} kg, found {3} kg.").format(
                item_code, warehouse, flt(required_qty), flt(required_qty) - remaining
            )
        )
    return alloc

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
    def autoname(self):
        """Normal outturns are named by their outturn number (booking name),
        e.g. 16EM0001 — batches then read 16EM0001-AA. Super Outturns keep
        the naming series."""
        if self.outturn_type != "Super Outturn" and self.outturn_number:
            if frappe.db.exists("Outturn Statement", self.outturn_number):
                frappe.throw(
                    _("An Outturn Statement named {0} already exists.").format(self.outturn_number)
                )
            self.name = self.outturn_number

    def validate(self):
        self._fetch_parchment_weight()
        self._calculate_grade_weights()
        self._calculate_milling_loss()
        self._map_grade_items()

    def _fetch_parchment_weight(self):
        # SO-based auto-fill (current flow):
        #   Super Outturn → sum row.parchment_weight across component rows;
        #                   grower is the join of all component customers.
        #   Normal        → parchment_weight sums the SO's parchment types;
        #                   grower = SO.customer.
        # Legacy Booking fallback runs only if the Booking doctype is still
        # present in the DB (transitional installs).
        if self.outturn_type == "Super Outturn":
            total = 0
            growers = []
            for row in (self.component_bookings or []):
                # Prefer the new Sales Order link; fall back to legacy row.grower.
                if row.get("sales_order"):
                    if not row.get("parchment_weight"):
                        # Derive parchment weight from the SO's parchment types.
                        types = frappe.get_all(
                            "Endebess Parchment Type",
                            filters={"parent": row.sales_order, "parenttype": "Sales Order"},
                            fields=["expected_weight_kg"],
                        )
                        row.parchment_weight = sum(flt(t.expected_weight_kg) for t in types)
                    if not row.get("grower"):
                        row.grower = frappe.db.get_value("Sales Order", row.sales_order, "customer")
                total += flt(row.get("parchment_weight"))
                if row.get("grower") and row.grower not in growers:
                    growers.append(row.grower)

            # Legacy Booking hydration for any pre-existing rows without SO.
            if _booking_exists():
                for row in (self.component_bookings or []):
                    if row.get("sales_order") or not row.get("booking_outturn"):
                        continue
                    booking = frappe.get_value(
                        "Booking", row.booking_outturn, ["net_weight", "grower"], as_dict=True
                    )
                    if booking:
                        total += booking.net_weight or 0
                        if booking.grower and booking.grower not in growers:
                            growers.append(booking.grower)

            self.parchment_weight = total
            self.grower = ", ".join(growers) if growers else self.grower
            return

        # Normal outturn — SO-based path first.
        if self.get("custom_source_sales_order"):
            so = frappe.db.get_value(
                "Sales Order", self.custom_source_sales_order,
                ["customer", "custom_outturn_number"], as_dict=True,
            )
            if so:
                if not self.outturn_number:
                    self.outturn_number = so.custom_outturn_number
                if not self.grower:
                    self.grower = so.customer
                if not self.parchment_weight:
                    types = frappe.get_all(
                        "Endebess Parchment Type",
                        filters={"parent": self.custom_source_sales_order, "parenttype": "Sales Order"},
                        fields=["expected_weight_kg"],
                    )
                    self.parchment_weight = sum(flt(t.expected_weight_kg) for t in types)
                return

        # Legacy Booking fallback for Normal outturns.
        if _booking_exists() and self.outturn_number:
            booking = frappe.get_value(
                "Booking", self.outturn_number, ["net_weight", "grower"], as_dict=True
            )
            if booking:
                self.parchment_weight = booking.net_weight or 0
                self.grower = booking.grower or self.grower

    def _calculate_grade_weights(self):
        bag_kg = _bag_kg()
        for row in self.table_cyvh:
            row.net_weight = (row.no_of_bags or 0) * bag_kg + (row.no_of_pockets or 0)

    def _calculate_milling_loss(self):
        if self.parchment_weight and self.parchment_weight > 0 and self.output_weight is not None:
            self.milling_loss = ((self.parchment_weight - self.output_weight) / self.parchment_weight) * 100

    def _map_grade_items(self):
        # `grade` is now a direct Link → Item, so item_code equals grade.
        # GRADE_ITEM_MAP is still consulted as a fallback for any legacy
        # rows whose grade was persisted as a bare code (AA / AB / …)
        # before the Select → Link migration.
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
        bag_kg = _bag_kg()
        total_grade_weight = sum(
            (row.no_of_bags or 0) * bag_kg + (row.no_of_pockets or 0) for row in self.table_cyvh
        )
        if abs(total_grade_weight - self.output_weight) > 1:
            frappe.throw(
                _("Grade breakdown total ({0} kg) does not match output weight ({1} kg). Please reconcile.").format(
                    total_grade_weight, self.output_weight
                )
            )

        # milling cannot create coffee: output can never exceed the parchment in
        if self.parchment_weight and self.output_weight > self.parchment_weight:
            frappe.throw(
                _("Output ({0} kg) cannot exceed the parchment that went in ({1} kg).").format(
                    self.output_weight, self.parchment_weight
                )
            )

    def on_submit(self):
        pass  # handled by doc_events


def on_submit_create_milled_stock(doc, method):
    """Create Repack Stock Entry: Parchment -> Graded Coffee items.
    Consumes the INPUT parchment weight (milling loss is inherent: output
    weighs less); prefers the outturn's own batch, then FIFO."""
    settings = _settings()
    # Legacy: parchment_type used to live on Booking. If Booking is gone the
    # lookup returns None and we fall back to Coffee Settings' default
    # parchment item.
    ptype = None
    if doc.outturn_number and _booking_exists():
        ptype = frappe.db.get_value("Booking", doc.outturn_number, "parchment_type")
    parchment_item = parchment_item_for(ptype)
    consume_qty = doc.parchment_weight or doc.output_weight
    dry_mill_stock = _get_warehouse_stock(parchment_item, settings.dry_mill_warehouse)
    if dry_mill_stock < consume_qty:
        frappe.throw(
            _("Insufficient parchment in {0}. Available: {1} kg, Required: {2} kg.").format(
                settings.dry_mill_warehouse, dry_mill_stock, consume_qty
            )
        )

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Repack"
    se.posting_date = frappe.utils.today()
    se.company = frappe.db.get_value("Warehouse", settings.dry_mill_warehouse, "company")
    # Use doc.name as the unique reference in remarks so cancel can find it reliably
    se.remarks = f"Milling output for {doc.name}"

    # Source: parchment from dry mill — own outturn batch first, then FIFO
    alloc = _allocate_batches(
        parchment_item, settings.dry_mill_warehouse, consume_qty,
        prefer_batch=doc.outturn_number,
    )
    for batch_no, qty in alloc:
        se.append(
            "items",
            {
                "item_code": parchment_item,
                "qty": qty,
                "uom": "Kilogram",
                "s_warehouse": settings.dry_mill_warehouse,
                "batch_no": batch_no,
                "use_serial_batch_fields": 1,
                "allow_zero_valuation_rate": 1,
            },
        )

    # Finished goods: each grade goes to milled store
    # multiple finished goods in a Repack need manual rates (ERPNext v16):
    # spread the consumed parchment value uniformly per output kg
    parchment_rate = flt(frappe.db.get_value(
        "Bin", {"warehouse": settings.dry_mill_warehouse, "item_code": parchment_item},
        "valuation_rate")) or 0
    out_rate = (parchment_rate * consume_qty / doc.output_weight) if doc.output_weight else 0

    bag_kg = _bag_kg()
    for row in doc.table_cyvh:
        weight = (row.no_of_bags or 0) * bag_kg + (row.no_of_pockets or 0)
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
                    "t_warehouse": settings.milled_store_warehouse,
                    "is_finished_item": 1,
                    "set_basic_rate_manually": 1,
                    "basic_rate": out_rate,
                    "batch_no": batch_id,
                    "use_serial_batch_fields": 1,
                },
            )

    se.insert(ignore_permissions=True)
    se.submit()

    # Update legacy Booking status (skip entirely if Booking is gone).
    if _booking_exists():
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
            se.name, doc.output_weight, settings.milled_store_warehouse
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

    # Reset legacy Booking statuses (skip if Booking is gone).
    if _booking_exists():
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

    # Determine customer and is_internal.
    # Preferred source (current): the linked Sales Order on custom_source_sales_order.
    # Legacy fallback: read grower/is_internal from Booking if it's still present.
    booking = None
    if doc.get("custom_source_sales_order"):
        so_row = frappe.db.get_value(
            "Sales Order", doc.custom_source_sales_order,
            ["customer as grower"], as_dict=True,
        )
        if so_row and so_row.grower:
            is_internal_flag = frappe.db.get_value(
                "Customer", so_row.grower, "is_internal_customer"
            )
            booking = frappe._dict({"grower": so_row.grower, "is_internal": is_internal_flag or 0})

    if not booking and _booking_exists():
        if doc.outturn_type == "Super Outturn":
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
        frappe.throw(_(
            "No grower could be resolved for this outturn — link a Sales Order via "
            "<b>Source Sales Order</b> or (legacy) a Booking."
        ))

    is_internal = cint(booking.is_internal)

    dn = frappe.new_doc("Delivery Note")
    dn.customer = booking.grower
    dn.company = COMPANY
    dn.currency = "USD"
    dn.posting_date = frappe.utils.today()
    dn.custom_outturn_references = doc.outturn_number or doc.name

    # Both internal and outgrower invoices are processed at Endebess mill
    dn.custom_farm = "Endebess"
    dn.custom_business_unit = "Endebess Coffee"
    dn.custom_location = "Endebess"
    dn.custom_delivery_type = "Coffee Dispatch"

    # Override the fetch_from on set_warehouse — the Coffee Dispatch delivery type
    # record has the wrong source_warehouse ("Yogurt Coldroom - KR")
    dn.set_warehouse = MILLED_STORE_WH

    if is_internal:
        # Endebess: Coffee Dispatch header — items added manually via the DN form
        pass
    else:
        # Ensure service charge item UOMs allow decimals before inserting
        _ensure_service_item_uoms()
        # Outgrower: service charges only — no stock items, no warehouse
        _add_outgrower_charge_items(dn, doc)

    dn.insert(ignore_permissions=True)
    frappe.db.set_value(
        "Outturn Statement", outturn_name, "linked_delivery_note", dn.name, update_modified=False
    )
    return dn.name


def _add_outgrower_charge_items(dn, outturn_doc):
    """Append service charge items to an outgrower Delivery Note.

    Quantities and rates mirror the Outturn Statement print format:
      Milling:      parchment_weight / 1000 tonnes  × $45/tonne
      Handling:     parchment_weight / 60   bags    × $1.50/bag
      Export Bags:  export_qty              bags    × $3.50/bag + 16% VAT
      Transport:    output_weight / 60      bags    × $3.00/bag
    """
    parchment = outturn_doc.parchment_weight or 0
    output = outturn_doc.output_weight or 0
    outturn_ref = outturn_doc.outturn_number or outturn_doc.name

    # Export bag qty: each grade row = no_of_bags + 1 if it has a pocket
    export_qty = sum(
        (row.no_of_bags or 0) + (1 if (row.no_of_pockets or 0) > 0 else 0)
        for row in outturn_doc.table_cyvh
    )

    charges = [
        (
            "COFFEE-MILLING",
            "Milling Charges",
            round(parchment / 1000, 3),
            45.00,
            "Tonne",
        ),
        (
            "COFFEE-HANDLING",
            "Handling Charges",
            round(parchment / 60, 2),
            1.50,
            "Bags",
        ),
        (
            "COFFEE-EXPORT-BAGS",
            "Export Bags + VAT (16%)",
            export_qty,
            3.50,
            "Bags",
        ),
    ]

    if outturn_doc.transport_expenses:
        charges.append((
            "COFFEE-TRANSPORT",
            "Transport",
            round(output / 60, 2),
            3.00,
            "Bags",
        ))

    for item_code, label, qty, rate, uom in charges:
        dn.append(
            "items",
            {
                "item_code": item_code,
                "item_name": label,
                "qty": qty,
                "uom": uom,
                "rate": rate,
                "description": f"{label} — outturn {outturn_ref} ({outturn_doc.grower})",
            },
        )


_SERVICE_ITEM_UOMS = {
    "COFFEE-MILLING": "Tonne",
    "COFFEE-HANDLING": "Bags",
    "COFFEE-EXPORT-BAGS": "Bags",
    "COFFEE-TRANSPORT": "Bags",
}


def _ensure_service_item_uoms():
    """Ensure service charge items have a UOM that allows decimal quantities.
    Run inline at DN creation so it doesn't depend on migrate having been run.
    """
    for uom_name in ("Tonne", "Bags"):
        if not frappe.db.exists("UOM", uom_name):
            frappe.get_doc({
                "doctype": "UOM",
                "uom_name": uom_name,
                "must_be_whole_number": 0,
            }).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("UOM", uom_name, "must_be_whole_number", 0, update_modified=False)

    for item_code, target_uom in _SERVICE_ITEM_UOMS.items():
        if not frappe.db.exists("Item", item_code):
            continue
        frappe.db.set_value("Item", item_code, {
            "stock_uom": target_uom,
            "disabled": 0,
            "is_stock_item": 0,
        }, update_modified=False)
        if not frappe.db.exists("UOM Conversion Detail", {"parent": item_code, "uom": target_uom}):
            item = frappe.get_doc("Item", item_code)
            item.append("uoms", {"uom": target_uom, "conversion_factor": 1})
            item.save(ignore_permissions=True)

    frappe.db.commit()


@frappe.whitelist()
def create_outgrower_invoice(outturn_name):
    """Create a draft Sales Invoice with outgrower charge items (Milling, Handling, etc.)."""
    doc = frappe.get_doc("Outturn Statement", outturn_name)
    if doc.docstatus != 1:
        frappe.throw(_("Outturn Statement must be submitted first."))

    # Preferred source (current): the linked Sales Order. Legacy fallback: Booking.
    booking = None
    if doc.get("custom_source_sales_order"):
        so_row = frappe.db.get_value(
            "Sales Order", doc.custom_source_sales_order, ["customer as grower"], as_dict=True,
        )
        if so_row and so_row.grower:
            is_internal_flag = frappe.db.get_value(
                "Customer", so_row.grower, "is_internal_customer"
            )
            booking = frappe._dict({"grower": so_row.grower, "is_internal": is_internal_flag or 0})
    if not booking and _booking_exists() and doc.outturn_number:
        booking = frappe.get_value(
            "Booking", doc.outturn_number, ["grower", "is_internal"], as_dict=True,
        )
    if not booking or not booking.grower:
        frappe.throw(_(
            "No grower could be resolved for this outturn — link a Sales Order via "
            "<b>Source Sales Order</b> or (legacy) a Booking."
        ))

    # Ensure customer has default_currency = USD so ERPNext initialises the SI in USD.
    # Outgrowers always transact in USD; set this once if missing.
    if not frappe.db.get_value("Customer", booking.grower, "default_currency"):
        frappe.db.set_value("Customer", booking.grower, "default_currency", "USD", update_modified=False)

    si = frappe.new_doc("Sales Invoice")
    si.customer = booking.grower
    si.company = COMPANY
    si.currency = "USD"
    si.selling_price_list = "USD Price List"
    si.party_account_currency = "USD"
    si.posting_date = frappe.utils.today()
    si.custom_farm = "Endebess"
    si.custom_business_unit = "Endebess Coffee"
    si.update_stock = 0
    si.custom_outturn_number = doc.outturn_number

    _add_outgrower_charge_items(si, doc)

    # Explicitly set USD debit_to account so set_missing_values doesn't pick up KES default
    usd_receivable = frappe.db.get_value(
        "Party Account",
        {"parent": booking.grower, "parenttype": "Customer", "company": COMPANY},
        "account",
    )
    if usd_receivable:
        si.debit_to = usd_receivable

    si.insert(ignore_permissions=True, ignore_mandatory=True)
    return si.name


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
