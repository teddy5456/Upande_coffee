import frappe
from frappe.utils import flt
from frappe import _
from frappe.model.document import Document

from upande_coffee.stock import apply_coffee_defaults


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
        total = 0
        for row in self.table_cyvh:
            row.net_weight = (row.no_of_bags or 0) * bag_kg + (row.no_of_pockets or 0)
            total += row.net_weight
        # Output Weight is derived from the grade breakdown, not typed
        # separately -- before_submit already treats the grade total as the
        # ground truth (it throws if output_weight drifts from this same sum),
        # so there was never a second, independent source for this number.
        self.output_weight = total

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

    apply_coffee_defaults(se)
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
    """Draft Delivery Note billing an EXTERNAL grower for what actually
    happened. Same maths as the old Milling Invoice print format:

      Milling     = parchment milled (outturn parchment weight) / 1000      -> tonnes
      Handling    = parchment received (submitted Coffee Intakes) / bag kg  -> bags
      Export bags = grade bags + 1 per grade with pockets, issued from the
                    bag store, VAT 16% on that line only
      Transport   = output weight / bag kg, when the Sales Order (or the
                    outturn) asks for transport

    Rates: the grower's Sales Order line when it has one, else the Coffee
    Settings price list (Endebess Milling Charges USD on Kaitet). A Delivery
    Note rather than an invoice so the jute bags leave the store; the Sales
    Invoice is made from the DN. Estate (internal) outturns are not charged."""
    doc = frappe.get_doc("Outturn Statement", outturn_name)
    if doc.docstatus != 1:
        frappe.throw(_("Submit the Outturn Statement before creating its Delivery Note."))
    if doc.linked_delivery_note and frappe.db.get_value(
        "Delivery Note", doc.linked_delivery_note, "docstatus"
    ) in (0, 1):
        frappe.throw(_("Delivery Note {0} already exists for this outturn.").format(doc.linked_delivery_note))
    if not doc.get("custom_source_sales_order"):
        frappe.throw(_("Set the Source Sales Order on this outturn first - the charges come from it."))

    so = frappe.get_doc("Sales Order", doc.custom_source_sales_order)
    if frappe.db.get_value("Customer", so.customer, "is_internal_customer"):
        frappe.throw(_("{0} is an internal customer - estate coffee is not charged for milling.").format(so.customer))

    settings = frappe.get_cached_doc("Coffee Settings")
    bag_kg = flt(settings.bag_weight_kg) or 60
    arrived = flt(frappe.db.sql(
        """SELECT COALESCE(SUM(i.qty_kg), 0) FROM `tabCoffee Intake Item` i
        JOIN `tabCoffee Intake` c ON c.name = i.parent
        WHERE c.sales_order = %s AND c.docstatus = 1""",
        so.name,
    )[0][0])
    arrived_from = "received on Coffee Intake"
    milled, output = flt(doc.parchment_weight), flt(doc.output_weight)
    if not arrived:
        arrived, arrived_from = milled, "no submitted Coffee Intake, so the outturn parchment weight"
    export_bags = sum((g.no_of_bags or 0) + (1 if (g.no_of_pockets or 0) > 0 else 0) for g in doc.table_cyvh)
    bag_item = frappe.db.get_value("Item", {"item_name": "Coffee Jute Bags", "disabled": 0}, "name")
    vat_template, vat_account = _vat_16(so.company)

    # (item, keyword to find it on the SO, qty, description, warehouse, item tax template)
    specs = [
        (settings.endebess_milling_item, "milling", milled / 1000, f"Milling - {milled} kg parchment milled", None, None),
        (settings.endebess_handling_item, "handling", arrived / bag_kg,
            f"Handling - {arrived} kg {arrived_from} / {bag_kg} kg bags", None, None),
    ]
    if bag_item and export_bags:
        specs.append((bag_item, "bag", export_bags,
            f"Export bags - {export_bags} (grade bags + 1 per grade with pockets), VAT 16%",
            _fullest_bin(bag_item, so.company), vat_template))
    if so.get("custom_transport_expenses") or doc.get("transport_expenses"):
        specs.append((settings.endebess_transport_item, "transport", output / bag_kg,
            f"Transport - {output} kg clean / {bag_kg} kg bags", None, None))

    dn = frappe.new_doc("Delivery Note")
    dn.update({
        "customer": so.customer, "company": so.company, "currency": so.currency,
        "conversion_rate": so.conversion_rate or 1,
        "selling_price_list": settings.endebess_price_list or so.selling_price_list,
        "posting_date": frappe.utils.nowdate(),
        "instructions": f"Milling charges for outturn {doc.outturn_number or doc.name} ({so.name}): "
            f"{arrived} kg received, {milled} kg milled, {output} kg clean.",
    })
    # same rate both ways, or a USD note bills 45.10 for a 45.00 charge
    dn.price_list_currency = frappe.db.get_value("Price List", dn.selling_price_list, "currency")
    if dn.price_list_currency == dn.currency:
        dn.plc_conversion_rate = dn.conversion_rate

    for code, word, qty, description, warehouse, tax_template in specs:
        line = None if word == "bag" else _so_line(so, code, word)
        code = line.item_code if line else code
        if not code:
            continue
        row = {"item_code": code, "qty": flt(qty, 3), "description": description,
            "warehouse": warehouse, "item_tax_template": tax_template}
        if line and flt(line.rate) > 0:
            row.update(rate=flt(line.rate), uom=line.uom)
        dn.append("items", row)
    if vat_account and bag_item and export_bags:
        dn.append("taxes", {"charge_type": "On Net Total", "account_head": vat_account, "rate": 0,
            "included_in_print_rate": 0, "description": "VAT 16% (export bags)"})
    if not dn.items:
        frappe.throw(_("No charge items - set the Milling / Handling service items in Coffee Settings."))
    dn.insert()
    frappe.db.set_value("Outturn Statement", doc.name, "linked_delivery_note", dn.name, update_modified=False)
    return dn.name


def _so_line(so, code, word):
    """The Sales Order's charge line for this service: same item, else one whose
    name mentions it (older orders used other item codes for the same charge)."""
    return next((i for i in so.items if i.item_code == code), None) or next(
        (i for i in so.items if word in f"{i.item_name} {i.item_code}".lower()), None)


def _fullest_bin(item_code, company):
    rows = frappe.db.sql(
        """SELECT b.warehouse FROM `tabBin` b JOIN `tabWarehouse` w ON w.name = b.warehouse
        WHERE b.item_code = %s AND b.actual_qty > 0 AND w.company = %s
        ORDER BY b.actual_qty DESC LIMIT 1""",
        (item_code, company),
    )
    return rows[0][0] if rows else None


def _vat_16(company):
    """The company's active 16% Item Tax Template and its account. Only the bag
    line carries it, so the On Net Total row at 0% taxes nothing else."""
    rows = frappe.db.sql(
        """SELECT d.parent, d.tax_type FROM `tabItem Tax Template Detail` d
        JOIN `tabItem Tax Template` t ON t.name = d.parent
        WHERE t.company = %s AND d.tax_rate = 16 AND t.disabled = 0 LIMIT 1""",
        company,
    )
    return (rows[0][0], rows[0][1]) if rows else (None, None)


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
