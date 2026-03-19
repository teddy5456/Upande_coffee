import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {
            "label": _("Block"),
            "fieldname": "block",
            "fieldtype": "Link",
            "options": "Warehouse",
            "width": 220,
        },
        {
            "label": _("Harvest Days"),
            "fieldname": "harvest_days",
            "fieldtype": "Int",
            "width": 110,
        },
        {
            "label": _("Total Buckets"),
            "fieldname": "total_buckets",
            "fieldtype": "Int",
            "width": 120,
        },
        {
            "label": _("Cherry Weight (kg)"),
            "fieldname": "total_cherry_kg",
            "fieldtype": "Float",
            "precision": 1,
            "width": 150,
        },
        {
            "label": _("Avg kg / Harvest Day"),
            "fieldname": "avg_kg_day",
            "fieldtype": "Float",
            "precision": 1,
            "width": 160,
        },
        {
            "label": _("Avg Buckets / Day"),
            "fieldname": "avg_buckets_day",
            "fieldtype": "Float",
            "precision": 1,
            "width": 150,
        },
        {
            "label": _("Est. Labour Cost (KES)"),
            "fieldname": "estimated_cost",
            "fieldtype": "Currency",
            "options": "KES",
            "width": 170,
        },
        {
            "label": _("KES / kg Cherry"),
            "fieldname": "cost_per_kg",
            "fieldtype": "Float",
            "precision": 1,
            "width": 140,
        },
    ]


def get_columns_for_filters(filters):
    """Build WHERE conditions for both harvest and cost queries."""
    harvest_conds = [
        "hp.docstatus = 1",
        "hpd.block IS NOT NULL",
        "hpd.block != ''",
    ]
    cost_conds = [
        "hl.block IS NOT NULL",
        "hl.block != ''",
    ]
    params = {}

    if filters.get("start_date"):
        harvest_conds.append("hp.date >= %(start_date)s")
        cost_conds.append("hl.date >= %(start_date)s")
        params["start_date"] = filters.start_date

    if filters.get("end_date"):
        harvest_conds.append("hp.date <= %(end_date)s")
        cost_conds.append("hl.date <= %(end_date)s")
        params["end_date"] = filters.end_date

    return " AND ".join(harvest_conds), " AND ".join(cost_conds), params


def get_data(filters):
    harvest_conds, cost_conds, params = get_columns_for_filters(filters)

    harvest_rows = frappe.db.sql(
        f"""
        SELECT
            hpd.block,
            COUNT(DISTINCT hp.date)                  AS harvest_days,
            CAST(SUM(hpd.bucket_count) AS UNSIGNED)  AS total_buckets,
            ROUND(SUM(hpd.weight_kg), 1)             AS total_cherry_kg
        FROM `tabHarvest Pickup Detail` hpd
        JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
        WHERE {harvest_conds}
        GROUP BY hpd.block
        ORDER BY total_cherry_kg DESC
        """,
        params,
        as_dict=True,
    )

    cost_rows = frappe.db.sql(
        f"""
        SELECT
            hl.block,
            SUM(hl.bucket_count * IFNULL(cp_rate.rate, 0)) AS estimated_cost
        FROM `tabHarvest Log` hl
        LEFT JOIN (
            SELECT harvester_id, date, AVG(rate) AS rate
            FROM `tabCoffee Payment`
            WHERE docstatus = 1
            GROUP BY harvester_id, date
        ) cp_rate ON cp_rate.harvester_id = hl.harvester_id
                 AND cp_rate.date = hl.date
        WHERE {cost_conds}
        GROUP BY hl.block
        """,
        params,
        as_dict=True,
    )

    cost_map = {r.block: float(r.estimated_cost or 0) for r in cost_rows}

    result = []
    for row in harvest_rows:
        cherry_kg = float(row.total_cherry_kg or 0)
        days = int(row.harvest_days or 0)
        buckets = int(row.total_buckets or 0)
        cost = cost_map.get(row.block, 0.0)
        result.append(
            {
                "block": row.block,
                "harvest_days": days,
                "total_buckets": buckets,
                "total_cherry_kg": cherry_kg,
                "avg_kg_day": round(cherry_kg / days, 1) if days > 0 else 0,
                "avg_buckets_day": round(buckets / days, 1) if days > 0 else 0,
                "estimated_cost": round(cost, 2),
                "cost_per_kg": round(cost / cherry_kg, 1) if cherry_kg > 0 else 0,
            }
        )
    return result


def get_filters():
    return [
        {
            "fieldname": "start_date",
            "label": _("Season Start"),
            "fieldtype": "Date",
            "reqd": 1,
            "default": frappe.utils.add_months(frappe.utils.today(), -6),
        },
        {
            "fieldname": "end_date",
            "label": _("Season End"),
            "fieldtype": "Date",
            "reqd": 1,
            "default": frappe.utils.today(),
        },
    ]
