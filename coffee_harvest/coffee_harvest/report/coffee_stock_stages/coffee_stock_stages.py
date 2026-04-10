"""Coffee Stock at Stages Report.

Shows a snapshot of all coffee in the pipeline:
  Stage 1 – Cherry in Wet Mill (submitted Harvest Pickups not yet dried)
  Stage 2 – Coffee on Drying Tables (active Drying Assignments)
  Stage 3 – Parchment in Bins (submitted Drying Assignments not yet milled)
  Stage 4 – Clean Coffee (submitted Outturn Statements not yet fully dispatched)
"""

import frappe
from frappe import _


WET_MILL_WH = "Coffee Wet Mill - KL"
CHERRY_ITEM = "Coffee-Cherry-Batched"
PARCHMENT_ITEM = "COFFEE-PARCHMENT"
COMPANY = "Kaitet Ltd."


def execute(filters=None):
    columns = _get_columns()
    data = _get_data()
    chart = _get_chart(data)
    summary = _get_summary(data)
    return columns, data, None, chart, summary


def _get_columns():
    return [
        {"label": _("Stage"), "fieldname": "stage", "fieldtype": "Data", "width": 180},
        {"label": _("Reference / Batch"), "fieldname": "reference", "fieldtype": "Data", "width": 200},
        {"label": _("Detail"), "fieldname": "detail", "fieldtype": "Data", "width": 220},
        {"label": _("Weight (kg)"), "fieldname": "weight_kg", "fieldtype": "Float", "precision": 1, "width": 120},
        {"label": _("Status / Notes"), "fieldname": "notes", "fieldtype": "Data", "width": 200},
    ]


def _section_header(label):
    return {
        "stage": label,
        "reference": None,
        "detail": None,
        "weight_kg": None,
        "notes": "",
        "bold": 1,
        "is_group": 1,
    }


def _row(stage, reference, detail, weight_kg, notes=""):
    return {
        "stage": stage,
        "reference": reference,
        "detail": detail,
        "weight_kg": round(float(weight_kg or 0), 1),
        "notes": notes,
    }


# ─────────────────────────────────────────────────────────────────────────────

def _get_data():
    data = []

    # ── Stage 1: Cherry in Wet Mill ──────────────────────────────────────────
    cherry_batches = frappe.db.sql(
        """
        SELECT sle.batch_no,
               SUM(sle.actual_qty) AS qty
        FROM `tabStock Ledger Entry` sle
        WHERE sle.item_code = %(cherry_item)s
          AND sle.warehouse = %(warehouse)s
          AND sle.is_cancelled = 0
        GROUP BY sle.batch_no
        HAVING SUM(sle.actual_qty) > 0.01
        ORDER BY sle.batch_no
        """,
        {"cherry_item": CHERRY_ITEM, "warehouse": WET_MILL_WH},
        as_dict=True,
    )

    cherry_total = sum(float(r.qty or 0) for r in cherry_batches)
    data.append(_section_header(f"Stage 1 — Cherry in Wet Mill  ({round(cherry_total, 1)} kg)"))
    for r in cherry_batches:
        data.append(_row("", r.batch_no, "Cherry batch", r.qty, WET_MILL_WH))
    if not cherry_batches:
        data.append(_row("", "—", "No cherry in wet mill", 0))

    # ── Stage 2: Coffee on Drying Tables ────────────────────────────────────
    active_assignments = frappe.db.sql(
        """
        SELECT da.name, da.batch, da.start_date,
               da.total_initial_weight_kg, da.drying_status,
               GROUP_CONCAT(dte.drying_table ORDER BY dte.idx SEPARATOR ', ') AS tables
        FROM `tabDrying Assignment` da
        JOIN `tabDrying Table Entry` dte ON dte.parent = da.name
        WHERE da.docstatus = 0
          AND da.drying_status = 'In Progress'
        GROUP BY da.name
        ORDER BY da.start_date DESC
        """,
        as_dict=True,
    )

    drying_total = sum(float(r.total_initial_weight_kg or 0) for r in active_assignments)
    data.append(_section_header(f"Stage 2 — On Drying Tables  ({round(drying_total, 1)} kg initial)"))
    for r in active_assignments:
        data.append(
            _row(
                "",
                r.name,
                f"Batch: {r.batch}",
                r.total_initial_weight_kg,
                f"Tables: {r.tables or '—'}  |  Since: {r.start_date}",
            )
        )
    if not active_assignments:
        data.append(_row("", "—", "No active drying assignments", 0))

    # ── Stage 3: Parchment in Bins ───────────────────────────────────────────
    parchment_bins = frappe.db.sql(
        """
        SELECT sle.warehouse,
               SUM(sle.actual_qty) AS qty
        FROM `tabStock Ledger Entry` sle
        WHERE sle.item_code = %(parch_item)s
          AND sle.is_cancelled = 0
        GROUP BY sle.warehouse
        HAVING SUM(sle.actual_qty) > 0.01
        ORDER BY qty DESC
        """,
        {"parch_item": PARCHMENT_ITEM},
        as_dict=True,
    )

    parch_total = sum(float(r.qty or 0) for r in parchment_bins)
    data.append(_section_header(f"Stage 3 — Parchment in Bins  ({round(parch_total, 1)} kg)"))
    for r in parchment_bins:
        data.append(_row("", r.warehouse, "Parchment", r.qty))
    if not parchment_bins:
        data.append(_row("", "—", "No parchment in bins", 0))

    # ── Stage 4: Clean Coffee (Outturn Statements not fully dispatched) ──────
    clean_rows = frappe.db.sql(
        """
        SELECT os.name, os.grower, os.output_weight,
               os.booking,
               COALESCE(dn.name, '') AS delivery_note,
               COALESCE(si.name, '') AS sales_invoice
        FROM `tabOutturn Statement` os
        LEFT JOIN `tabDelivery Note Item` dni ON dni.against_sales_order = os.booking
        LEFT JOIN `tabDelivery Note` dn ON dn.name = dni.parent AND dn.docstatus < 2
        LEFT JOIN `tabSales Invoice` si
               ON si.custom_outturn_number = os.name AND si.docstatus < 2
        WHERE os.docstatus = 1
        GROUP BY os.name
        ORDER BY os.creation DESC
        """,
        as_dict=True,
    )

    clean_total = sum(float(r.output_weight or 0) for r in clean_rows)
    data.append(_section_header(f"Stage 4 — Clean Coffee (Outturn)  ({round(clean_total, 1)} kg)"))
    for r in clean_rows:
        dispatched = bool(r.delivery_note)
        invoiced = bool(r.sales_invoice)
        status_parts = []
        if dispatched:
            status_parts.append(f"DN: {r.delivery_note}")
        else:
            status_parts.append("No DN")
        if invoiced:
            status_parts.append(f"SI: {r.sales_invoice}")
        else:
            status_parts.append("No SI")
        detail = f"Grower: {r.grower}" if r.grower else "Estate"
        data.append(_row("", r.name, detail, r.output_weight, "  |  ".join(status_parts)))
    if not clean_rows:
        data.append(_row("", "—", "No submitted outturn statements", 0))

    return data


def _get_chart(data):
    labels = []
    values = []
    stage_map = {}
    for r in data:
        stage = r.get("stage", "")
        if stage.startswith("Stage"):
            # extract stage name up to the first em-dash or bracket
            label = stage.split("  (")[0].strip()
            # get total from header text
            try:
                kg = float(stage.split("(")[1].split(" ")[0])
            except Exception:
                kg = 0
            stage_map[label] = kg

    if not stage_map:
        return None

    labels = list(stage_map.keys())
    values = [stage_map[l] for l in labels]

    return {
        "data": {"labels": labels, "datasets": [{"name": "Weight (kg)", "values": values}]},
        "type": "bar",
        "height": 280,
        "colors": ["#2d6a3f"],
    }


def _get_summary(data):
    totals = {}
    for r in data:
        stage = r.get("stage", "")
        if stage.startswith("Stage"):
            try:
                kg = float(stage.split("(")[1].split(" ")[0])
            except Exception:
                kg = 0
            if "Stage 1" in stage:
                totals["cherry"] = kg
            elif "Stage 2" in stage:
                totals["drying"] = kg
            elif "Stage 3" in stage:
                totals["parch"] = kg
            elif "Stage 4" in stage:
                totals["clean"] = kg

    return [
        {"value": totals.get("cherry", 0), "label": "Cherry in Wet Mill (kg)", "datatype": "Float", "indicator": "Blue"},
        {"value": totals.get("drying", 0), "label": "On Drying Tables (kg initial)", "datatype": "Float", "indicator": "Orange"},
        {"value": totals.get("parch", 0), "label": "Parchment in Bins (kg)", "datatype": "Float", "indicator": "Yellow"},
        {"value": totals.get("clean", 0), "label": "Clean Coffee Ready (kg)", "datatype": "Float", "indicator": "Green"},
    ]
