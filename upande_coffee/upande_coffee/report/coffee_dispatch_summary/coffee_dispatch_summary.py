"""Coffee Dispatch Summary.

Mirrors the Endebess "DISPATCH, GRN & INV" spreadsheet: one row per
Delivery Note line item, showing Delivery Date, Outturn No., Grade,
Bags, Kgs, 60kg-bag equivalent, linked Sales Invoice and amount.

A `scope` filter lets the user switch between Endebess-only, No Endebess
(outgrowers only), or All.
"""

import frappe
from frappe import _


COFFEE_GRADES = ("AA", "AB", "C", "PB", "E", "T", "TT", "MH", "ML",
                 "NH", "NL", "HE", "UG", "UG1", "UG2")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"label": _("Delivery Date"), "fieldname": "delivery_date", "fieldtype": "Date", "width": 105},
        {"label": _("Outturn No."), "fieldname": "outturn_number", "fieldtype": "Link", "options": "Outturn Statement", "width": 120},
        {"label": _("Grower"), "fieldname": "grower", "fieldtype": "Data", "width": 180},
        {"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 90},
        {"label": _("Grade"), "fieldname": "grade", "fieldtype": "Data", "width": 70},
        {"label": _("Bags"), "fieldname": "bags", "fieldtype": "Float", "precision": 0, "width": 70},
        {"label": _("Kgs"), "fieldname": "kgs", "fieldtype": "Float", "precision": 1, "width": 95},
        {"label": _("60kg Bag Equiv."), "fieldname": "bags_60kg", "fieldtype": "Float", "precision": 2, "width": 120},
        {"label": _("Delivery Note"), "fieldname": "delivery_note", "fieldtype": "Link", "options": "Delivery Note", "width": 160},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 180},
        {"label": _("Sales Invoice"), "fieldname": "sales_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 155},
        {"label": _("SI Status"), "fieldname": "si_status", "fieldtype": "Data", "width": 90},
        {"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "options": "currency", "width": 90},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 75},
    ]


def get_data(filters):
    conditions = [
        "dn.docstatus = 1",
        "dn.custom_delivery_type = 'Coffee Dispatch'",
    ]
    params = {}

    if filters.get("from_date"):
        conditions.append("dn.posting_date >= %(from_date)s")
        params["from_date"] = filters.from_date

    if filters.get("to_date"):
        conditions.append("dn.posting_date <= %(to_date)s")
        params["to_date"] = filters.to_date

    if filters.get("customer"):
        conditions.append("dn.customer = %(customer)s")
        params["customer"] = filters.customer

    if filters.get("grade"):
        conditions.append("UPPER(dni.item_code) = UPPER(%(grade)s)")
        params["grade"] = filters.grade

    where = " AND ".join(conditions)

    # Pick ONE best Sales Invoice Item per DN line: prefer submitted
    # (docstatus=1) over draft, then most-recent SI by name.
    rows = frappe.db.sql(
        f"""
        SELECT
            dn.name                          AS delivery_note,
            dn.posting_date                  AS delivery_date,
            dn.customer                      AS customer,
            dn.currency                      AS currency,
            dni.name                         AS dni_name,
            dni.item_code                    AS grade,
            dni.batch_no                     AS batch_no,
            dni.qty                          AS bags,
            COALESCE(dni.stock_qty, dni.qty) AS kgs_raw,
            dni.uom                          AS uom,
            dni.rate                         AS rate,
            dni.amount                       AS amount,
            dni.warehouse                    AS warehouse,
            dni.against_sales_invoice        AS si_from_item,
            (
                SELECT sii.parent
                FROM `tabSales Invoice Item` sii
                WHERE sii.delivery_note = dn.name
                  AND sii.dn_detail   = dni.name
                  AND sii.docstatus  != 2
                ORDER BY sii.docstatus DESC, sii.creation DESC
                LIMIT 1
            )                                AS si_from_lookup,
            (
                SELECT sii.docstatus
                FROM `tabSales Invoice Item` sii
                WHERE sii.delivery_note = dn.name
                  AND sii.dn_detail   = dni.name
                  AND sii.docstatus  != 2
                ORDER BY sii.docstatus DESC, sii.creation DESC
                LIMIT 1
            )                                AS si_docstatus,
            b.is_internal                    AS is_internal,
            os.grower                        AS grower,
            os.name                          AS outturn_number
        FROM `tabDelivery Note` dn
        JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        LEFT JOIN `tabOutturn Statement` os
            ON dni.batch_no IS NOT NULL
           AND dni.batch_no != ''
           AND os.name = SUBSTRING_INDEX(dni.batch_no, '-', 1)
        LEFT JOIN `tabBooking` b
            ON b.name = os.outturn_number
        WHERE {where}
        ORDER BY dn.posting_date DESC, dn.name, dni.idx
        """,
        params,
        as_dict=True,
    )

    scope = (filters.get("scope") or "All").lower()
    si_label = {0: "Draft", 1: "Submitted"}
    result = []
    for r in rows:
        grade_up = (r.grade or "").upper().strip()
        if grade_up not in COFFEE_GRADES:
            # Skip non-grade rows (e.g. Transport, Handling, Milling charge items)
            continue

        grower = r.grower or ""
        customer_up = (r.customer or "").upper()
        # Endebess classification:
        #  1. Explicit: booking.is_internal = 1 via outturn trace
        #  2. Explicit: grower name contains ENDEBESS
        #  3. Fallback for missing batch_no: Endebess ships its own internal
        #     clean coffee (no outturn link) — treat as Endebess unless
        #     the customer is clearly an outgrower coffee farmer.
        is_endebess = bool(r.is_internal) or "ENDEBESS" in grower.upper()
        if not r.outturn_number and not is_endebess:
            # No outturn linkage — default to Endebess (internal stock)
            # unless the *customer* is itself an outgrower (unusual case).
            is_endebess = True

        if scope == "endebess only" and not is_endebess:
            continue
        if scope == "no endebess" and is_endebess:
            continue

        kgs = float(r.kgs_raw or 0)
        # Kg UOM rows already have stock_qty in kg; Bag UOM rows need qty*weight.
        # Use stock_qty (always kg) where possible; fall back to bags * 60.
        if not kgs and r.uom and r.uom.lower() in ("bag", "bags"):
            kgs = float(r.bags or 0) * 60

        bags = float(r.bags or 0)
        if r.uom and r.uom.lower() in ("kg", "kgs", "kilogram"):
            # qty is in kg in this case — derive bag count from kgs / 60
            kgs = kgs or float(r.bags or 0)
            bags = kgs / 60.0 if kgs else 0

        sales_invoice = r.si_from_item or r.si_from_lookup
        si_status = ""
        if sales_invoice:
            si_status = si_label.get(r.si_docstatus, "Draft") if r.si_docstatus is not None else ""

        result.append({
            "delivery_date":   r.delivery_date,
            "outturn_number":  r.outturn_number,
            "grower":          grower or ("Endebess" if is_endebess else ""),
            "source":          "Endebess" if is_endebess else "Outgrower",
            "grade":           grade_up,
            "bags":            round(bags, 0) if r.uom and r.uom.lower() in ("bag", "bags") else round(bags, 2),
            "kgs":             round(kgs, 1),
            "bags_60kg":       round(kgs / 60.0, 2) if kgs else 0,
            "delivery_note":   r.delivery_note,
            "customer":        r.customer,
            "sales_invoice":   sales_invoice,
            "si_status":       si_status,
            "rate":            r.rate,
            "amount":          r.amount,
            "currency":        r.currency or "KES",
        })

    return result


def get_chart(data):
    if not data:
        return None
    grade_kg = {}
    for r in data:
        g = r["grade"] or "-"
        grade_kg[g] = grade_kg.get(g, 0) + (r.get("kgs") or 0)
    grade_kg = dict(sorted(grade_kg.items(), key=lambda x: x[1], reverse=True))
    return {
        "data": {
            "labels": list(grade_kg.keys()),
            "datasets": [{"name": "Kgs Dispatched", "values": [round(v, 1) for v in grade_kg.values()]}],
        },
        "type": "bar",
        "fieldtype": "Float",
        "colors": ["#6b4423"],
        "height": 260,
    }


def get_summary(data):
    if not data:
        return []
    total_bags = sum(r.get("bags") or 0 for r in data)
    total_kgs = sum(r.get("kgs") or 0 for r in data)
    bags_60 = sum(r.get("bags_60kg") or 0 for r in data)
    endebess_kgs = sum(r.get("kgs") or 0 for r in data if r.get("source") == "Endebess")
    outgrower_kgs = total_kgs - endebess_kgs
    invoiced = sum(r.get("amount") or 0 for r in data if r.get("sales_invoice"))

    return [
        {"value": len(data), "label": "Line Items", "datatype": "Int", "indicator": "Blue"},
        {"value": round(total_bags), "label": "Total Bags", "datatype": "Int", "indicator": "Blue"},
        {"value": round(total_kgs, 1), "label": "Total Kgs", "datatype": "Float", "indicator": "Green"},
        {"value": round(bags_60, 2), "label": "60kg Bag Equiv.", "datatype": "Float", "indicator": "Green"},
        {"value": round(endebess_kgs, 1), "label": "Endebess Kgs", "datatype": "Float", "indicator": "Orange"},
        {"value": round(outgrower_kgs, 1), "label": "Outgrower Kgs", "datatype": "Float", "indicator": "Blue"},
        {"value": round(invoiced, 2), "label": "Invoiced Amount", "datatype": "Currency", "indicator": "Green"},
    ]
