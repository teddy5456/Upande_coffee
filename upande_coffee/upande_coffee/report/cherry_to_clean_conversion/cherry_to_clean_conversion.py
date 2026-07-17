"""Cherry to Clean Coffee Conversion Report.

Shows the full yield chain for a season:
  Cherry Harvested → Parchment Produced → Clean Coffee Milled

Rows:
  Stage header rows (bold, highlighted) — Cherry / Parchment / Clean Coffee totals
  Grade detail rows — per-grade breakdown of clean coffee output
  Per-block cherry breakdown section
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    data = get_data(filters)
    chart = _get_chart(data)
    summary = _get_summary(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {
            "label": _("Stage / Item"),
            "fieldname": "stage",
            "fieldtype": "Data",
            "width": 300,
        },
        {
            "label": _("Weight In (kg)"),
            "fieldname": "weight_in",
            "fieldtype": "Float",
            "precision": 1,
            "width": 140,
        },
        {
            "label": _("Weight Out (kg)"),
            "fieldname": "weight_out",
            "fieldtype": "Float",
            "precision": 1,
            "width": 140,
        },
        {
            "label": _("Yield %"),
            "fieldname": "yield_pct",
            "fieldtype": "Percent",
            "width": 100,
        },
        {
            "label": _("Loss (kg)"),
            "fieldname": "loss_kg",
            "fieldtype": "Float",
            "precision": 1,
            "width": 110,
        },
        {
            "label": _("Notes"),
            "fieldname": "notes",
            "fieldtype": "Data",
            "width": 240,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pct(out, inp):
    return round(out / inp * 100, 1) if inp and inp > 0 else 0.0


def _loss(inp, out):
    return round(inp - out, 1) if inp else 0.0


def _header_row(label, weight_in, weight_out, notes=""):
    return {
        "stage": label,
        "weight_in": weight_in,
        "weight_out": weight_out,
        "yield_pct": _pct(weight_out, weight_in),
        "loss_kg": _loss(weight_in, weight_out),
        "notes": notes,
        "bold": 1,
    }


def _detail_row(label, weight_out, weight_in_ref, notes=""):
    return {
        "stage": f"    {label}",
        "weight_in": None,
        "weight_out": weight_out,
        "yield_pct": _pct(weight_out, weight_in_ref),
        "loss_kg": None,
        "notes": notes,
    }


def _section_divider(label):
    return {
        "stage": label,
        "weight_in": None,
        "weight_out": None,
        "yield_pct": None,
        "loss_kg": None,
        "notes": "",
        "bold": 1,
        "is_group": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main data builder
# ─────────────────────────────────────────────────────────────────────────────

def get_data(filters):
    start = filters.get("start_date")
    end = filters.get("end_date")

    # ── 1. Cherry harvested (from submitted Harvest Pickups) ─────────────────
    cherry_result = frappe.db.sql(
        """
        SELECT ROUND(SUM(total_weight_kg), 1) AS total_cherry
        FROM `tabHarvest Pickup`
        WHERE docstatus = 1
          AND (%(start_date)s IS NULL OR date >= %(start_date)s)
          AND (%(end_date)s IS NULL OR date <= %(end_date)s)
        """,
        {"start_date": start, "end_date": end},
    )
    cherry_kg = float((cherry_result[0][0] or 0) if cherry_result else 0)

    # Cherry breakdown by block
    block_rows = frappe.db.sql(
        """
        SELECT hpd.block,
               ROUND(SUM(hpd.weight_kg), 1)             AS cherry_kg,
               CAST(SUM(hpd.bucket_count) AS UNSIGNED)  AS buckets
        FROM `tabHarvest Pickup Detail` hpd
        JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
        WHERE hp.docstatus = 1
          AND hpd.block IS NOT NULL AND hpd.block != ''
          AND (%(start_date)s IS NULL OR hp.date >= %(start_date)s)
          AND (%(end_date)s IS NULL OR hp.date <= %(end_date)s)
        GROUP BY hpd.block
        ORDER BY cherry_kg DESC
        """,
        {"start_date": start, "end_date": end},
        as_dict=True,
    )

    # ── 2. Parchment produced (from submitted Drying Assignments) ────────────
    parch_result = frappe.db.sql(
        """
        SELECT ROUND(SUM(total_final_weight_kg), 1) AS total_parch
        FROM `tabDrying Assignment`
        WHERE docstatus = 1
          AND (%(start_date)s IS NULL OR end_date >= %(start_date)s)
          AND (%(end_date)s IS NULL OR end_date <= %(end_date)s)
        """,
        {"start_date": start, "end_date": end},
    )
    parch_kg = float((parch_result[0][0] or 0) if parch_result else 0)

    # ── 3. Clean coffee milled (from submitted Outturn Statements) ───────────
    clean_result = frappe.db.sql(
        """
        SELECT ROUND(SUM(output_weight), 1) AS total_clean
        FROM `tabOutturn Statement`
        WHERE docstatus = 1
          AND (%(start_date)s IS NULL OR DATE(creation) >= %(start_date)s)
          AND (%(end_date)s IS NULL OR DATE(creation) <= %(end_date)s)
        """,
        {"start_date": start, "end_date": end},
    )
    clean_kg = float((clean_result[0][0] or 0) if clean_result else 0)

    # Grade breakdown from Outturn Details
    grade_rows = frappe.db.sql(
        """
        SELECT od.grade,
               ROUND(SUM(od.net_weight), 1) AS grade_kg,
               SUM(od.no_of_bags)           AS bags,
               SUM(od.no_of_pockets)        AS pockets
        FROM `tabOutturn Details` od
        JOIN `tabOutturn Statement` os ON os.name = od.parent
        WHERE os.docstatus = 1
          AND (%(start_date)s IS NULL OR DATE(os.creation) >= %(start_date)s)
          AND (%(end_date)s IS NULL OR DATE(os.creation) <= %(end_date)s)
          AND od.net_weight > 0
        GROUP BY od.grade
        ORDER BY grade_kg DESC
        """,
        {"start_date": start, "end_date": end},
        as_dict=True,
    )

    # ── Assemble rows ─────────────────────────────────────────────────────────
    data = []

    # Section 1: Conversion funnel
    data.append(_section_divider("── Conversion Funnel ──────────────────────────────────"))

    data.append(
        _header_row(
            "1. Cherry Harvested",
            cherry_kg,
            cherry_kg,
            "Source: Harvest Pickups (submitted)",
        )
    )

    data.append(
        _header_row(
            "2. Parchment Produced (after drying)",
            cherry_kg,
            parch_kg,
            "Source: Drying Assignments (submitted)",
        )
    )

    data.append(
        _header_row(
            "3. Clean Coffee Milled",
            parch_kg,
            clean_kg,
            "Source: Outturn Statements (submitted)",
        )
    )

    # Overall cherry → clean
    data.append(
        _header_row(
            "Overall: Cherry → Clean Coffee",
            cherry_kg,
            clean_kg,
            "Full-chain yield",
        )
    )

    # Section 2: Grade breakdown
    if grade_rows:
        data.append(_section_divider("── Grade Breakdown (Clean Coffee) ─────────────────────"))
        for g in grade_rows:
            gkg = float(g.grade_kg or 0)
            data.append(
                _detail_row(
                    f"Grade {g.grade}",
                    gkg,
                    clean_kg,
                    f"{int(g.bags or 0)} bags + {int(g.pockets or 0)} pockets",
                )
            )

    # Section 3: Per-block cherry
    if block_rows:
        data.append(_section_divider("── Cherry by Block ─────────────────────────────────────"))
        for b in block_rows:
            bkg = float(b.cherry_kg or 0)
            data.append(
                _detail_row(
                    b.block,
                    bkg,
                    cherry_kg,
                    f"{int(b.buckets or 0)} buckets",
                )
            )

    return data


def get_filters():
    return [
        {
            "fieldname": "start_date",
            "label": _("Season Start"),
            "fieldtype": "Date",
            "reqd": 0,
            "default": frappe.utils.add_months(frappe.utils.today(), -6),
        },
        {
            "fieldname": "end_date",
            "label": _("Season End"),
            "fieldtype": "Date",
            "reqd": 0,
            "default": frappe.utils.today(),
        },
    ]


def _get_chart(data):
    """Bar chart: Cherry → Parchment → Clean conversion funnel."""
    # Extract totals from the 3 funnel header rows
    funnel = {}
    for r in data:
        stage = r.get("stage", "")
        if stage.startswith("1. Cherry"):
            funnel["Cherry"] = r.get("weight_out", 0) or 0
        elif stage.startswith("2. Parchment"):
            funnel["Parchment"] = r.get("weight_out", 0) or 0
        elif stage.startswith("3. Clean"):
            funnel["Clean Coffee"] = r.get("weight_out", 0) or 0

    if not funnel:
        return None

    return {
        "data": {
            "labels": list(funnel.keys()),
            "datasets": [
                {"name": "Weight (kg)", "values": [round(v, 1) for v in funnel.values()]}
            ],
        },
        "type": "bar",
        "height": 260,
        "colors": ["#2d6a3f"],
        "fieldtype": "Float",
    }


def _get_summary(data):
    funnel = {}
    for r in data:
        stage = r.get("stage", "")
        if stage.startswith("1. Cherry"):
            funnel["cherry"] = r.get("weight_out", 0) or 0
        elif stage.startswith("2. Parchment"):
            funnel["parch"] = r.get("weight_out", 0) or 0
        elif stage.startswith("3. Clean"):
            funnel["clean"] = r.get("weight_out", 0) or 0

    if not funnel:
        return []

    cherry = funnel.get("cherry", 0)
    parch = funnel.get("parch", 0)
    clean = funnel.get("clean", 0)

    return [
        {"value": round(cherry, 1), "label": "Cherry Harvested (kg)", "datatype": "Float", "indicator": "Blue"},
        {"value": round(parch, 1), "label": "Parchment Produced (kg)", "datatype": "Float", "indicator": "Blue"},
        {"value": round(clean, 1), "label": "Clean Coffee Milled (kg)", "datatype": "Float", "indicator": "Green"},
        {
            "value": round(clean / cherry * 100, 1) if cherry else 0,
            "label": "Overall Yield %",
            "datatype": "Percent",
            "indicator": "Green",
        },
    ]
