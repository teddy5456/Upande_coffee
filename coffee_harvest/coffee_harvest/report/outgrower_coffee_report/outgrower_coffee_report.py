"""Outgrower Coffee Report.

Shows every submitted Outturn Statement for non-internal (outgrower) coffee,
with transport, delivery and invoice status.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


# ─────────────────────────────────────────────────────────────────────────────
# Columns
# ─────────────────────────────────────────────────────────────────────────────

def get_columns():
    return [
        {
            "label": _("Outturn No."),
            "fieldname": "outturn_name",
            "fieldtype": "Link",
            "options": "Outturn Statement",
            "width": 150,
        },
        {
            "label": _("Grower"),
            "fieldname": "grower",
            "fieldtype": "Link",
            "options": "Customer",
            "width": 220,
        },
        {
            "label": _("Date"),
            "fieldname": "outturn_date",
            "fieldtype": "Date",
            "width": 105,
        },
        {
            "label": _("Parchment (kg)"),
            "fieldname": "parchment_weight",
            "fieldtype": "Float",
            "precision": 1,
            "width": 120,
        },
        {
            "label": _("Clean Coffee (kg)"),
            "fieldname": "output_weight",
            "fieldtype": "Float",
            "precision": 1,
            "width": 130,
        },
        {
            "label": _("Milling Loss %"),
            "fieldname": "milling_loss",
            "fieldtype": "Percent",
            "width": 110,
        },
        {
            "label": _("Transport"),
            "fieldname": "has_transport",
            "fieldtype": "Data",
            "width": 90,
        },
        {
            "label": _("Delivery Note"),
            "fieldname": "linked_delivery_note",
            "fieldtype": "Link",
            "options": "Delivery Note",
            "width": 160,
        },
        {
            "label": _("DN Status"),
            "fieldname": "dn_status",
            "fieldtype": "Data",
            "width": 90,
        },
        {
            "label": _("Sales Invoice"),
            "fieldname": "sales_invoice",
            "fieldtype": "Link",
            "options": "Sales Invoice",
            "width": 155,
        },
        {
            "label": _("Invoice Amount"),
            "fieldname": "invoice_amount",
            "fieldtype": "Currency",
            "options": "invoice_currency",
            "width": 130,
        },
        {
            "label": _("Currency"),
            "fieldname": "invoice_currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 80,
        },
        {
            "label": _("Invoice Status"),
            "fieldname": "invoice_status",
            "fieldtype": "Data",
            "width": 100,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────

def get_data(filters):
    conditions = [
        "os.docstatus = 1",
        # outgrower = has a grower set and booking is non-internal
        "(os.grower IS NOT NULL AND os.grower != '')",
    ]
    params = {}

    if filters.get("start_date"):
        conditions.append("DATE(os.creation) >= %(start_date)s")
        params["start_date"] = filters.start_date

    if filters.get("end_date"):
        conditions.append("DATE(os.creation) <= %(end_date)s")
        params["end_date"] = filters.end_date

    if filters.get("grower"):
        conditions.append("os.grower = %(grower)s")
        params["grower"] = filters.grower

    where = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
        SELECT
            os.name                      AS outturn_name,
            os.grower,
            DATE(os.creation)            AS outturn_date,
            os.parchment_weight,
            os.output_weight,
            os.milling_loss,
            os.transport_expenses,
            os.linked_delivery_note,
            dn.docstatus                 AS dn_docstatus,
            si.name                      AS sales_invoice,
            si.grand_total               AS invoice_amount,
            si.currency                  AS invoice_currency,
            si.docstatus                 AS si_docstatus
        FROM `tabOutturn Statement` os
        LEFT JOIN `tabDelivery Note` dn
            ON dn.name = os.linked_delivery_note
        LEFT JOIN `tabSales Invoice` si
            ON si.custom_outturn_number = os.name
           AND si.docstatus != 2
        WHERE {where}
        ORDER BY DATE(os.creation) DESC, os.grower
        """,
        params,
        as_dict=True,
    )

    dn_label = {0: "Draft", 1: "Submitted", 2: "Cancelled"}
    si_label = {0: "Draft", 1: "Submitted"}

    result = []
    for r in rows:
        result.append(
            {
                "outturn_name": r.outturn_name,
                "grower": r.grower,
                "outturn_date": r.outturn_date,
                "parchment_weight": r.parchment_weight,
                "output_weight": r.output_weight,
                "milling_loss": r.milling_loss,
                "has_transport": "Yes" if r.transport_expenses else "No",
                "linked_delivery_note": r.linked_delivery_note,
                "dn_status": dn_label.get(r.dn_docstatus, "") if r.linked_delivery_note else "Not Created",
                "sales_invoice": r.sales_invoice,
                "invoice_amount": r.invoice_amount,
                "invoice_currency": r.invoice_currency,
                "invoice_status": si_label.get(r.si_docstatus, "") if r.sales_invoice else "Not Created",
            }
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Chart — Clean coffee kg per grower (bar)
# ─────────────────────────────────────────────────────────────────────────────

def get_chart(data):
    if not data:
        return None

    # Aggregate by grower
    grower_kg = {}
    for r in data:
        g = r.get("grower") or "Unknown"
        grower_kg[g] = grower_kg.get(g, 0) + (r.get("output_weight") or 0)

    grower_kg = dict(sorted(grower_kg.items(), key=lambda x: x[1], reverse=True))

    return {
        "data": {
            "labels": list(grower_kg.keys()),
            "datasets": [
                {"name": "Clean Coffee (kg)", "values": [round(v, 1) for v in grower_kg.values()]}
            ],
        },
        "type": "bar",
        "fieldtype": "Float",
        "colors": ["#2d6a3f"],
        "height": 280,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Summary strip
# ─────────────────────────────────────────────────────────────────────────────

def get_summary(data):
    if not data:
        return []

    total_parch = sum(r.get("parchment_weight") or 0 for r in data)
    total_clean = sum(r.get("output_weight") or 0 for r in data)
    with_transport = sum(1 for r in data if r.get("has_transport") == "Yes")
    with_dn = sum(1 for r in data if r.get("linked_delivery_note"))
    with_si = sum(1 for r in data if r.get("sales_invoice"))
    total_invoiced = sum(r.get("invoice_amount") or 0 for r in data)

    return [
        {"value": len(data), "label": "Outturns", "datatype": "Int", "indicator": "Blue"},
        {"value": round(total_parch, 1), "label": "Total Parchment (kg)", "datatype": "Float", "indicator": "Blue"},
        {"value": round(total_clean, 1), "label": "Total Clean Coffee (kg)", "datatype": "Float", "indicator": "Green"},
        {"value": with_transport, "label": "With Transport", "datatype": "Int", "indicator": "Blue"},
        {"value": with_dn, "label": "Delivery Notes Created", "datatype": "Int", "indicator": "Green" if with_dn == len(data) else "Orange"},
        {"value": with_si, "label": "Invoices Created", "datatype": "Int", "indicator": "Green" if with_si == len(data) else "Orange"},
        {"value": round(total_invoiced, 2), "label": "Total Invoiced (USD)", "datatype": "Currency", "indicator": "Green"},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────

def get_filters():
    return [
        {
            "fieldname": "start_date",
            "label": _("From Date"),
            "fieldtype": "Date",
            "default": frappe.utils.add_months(frappe.utils.today(), -12),
        },
        {
            "fieldname": "end_date",
            "label": _("To Date"),
            "fieldtype": "Date",
            "default": frappe.utils.today(),
        },
        {
            "fieldname": "grower",
            "label": _("Grower"),
            "fieldtype": "Link",
            "options": "Customer",
        },
    ]
