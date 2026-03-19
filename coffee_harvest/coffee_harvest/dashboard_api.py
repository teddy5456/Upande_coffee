import frappe


@frappe.whitelist()
def get_harvest_block_summary(start_date, end_date):
    """Return per-block harvest cherry volume and estimated labour cost for a date range.

    Harvest volume: from submitted Harvest Pickups → Harvest Pickup Detail.
    Labour cost: rate from Coffee Payment × buckets from Harvest Log, allocated per block.
    """
    harvest_rows = frappe.db.sql(
        """
        SELECT
            hpd.block,
            COUNT(DISTINCT hp.date)          AS harvest_days,
            CAST(SUM(hpd.bucket_count) AS UNSIGNED) AS total_buckets,
            ROUND(SUM(hpd.weight_kg), 1)     AS total_cherry_kg
        FROM `tabHarvest Pickup Detail` hpd
        JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
        WHERE hp.docstatus = 1
          AND hp.date BETWEEN %(start_date)s AND %(end_date)s
          AND hpd.block IS NOT NULL
          AND hpd.block != ''
        GROUP BY hpd.block
        ORDER BY total_cherry_kg DESC
        """,
        {"start_date": start_date, "end_date": end_date},
        as_dict=True,
    )

    # Estimate cost per block: rate (from Coffee Payment) × buckets (from Harvest Log)
    # Use average rate per harvester-day to handle partial-day payments.
    cost_rows = frappe.db.sql(
        """
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
        WHERE hl.date BETWEEN %(start_date)s AND %(end_date)s
          AND hl.block IS NOT NULL
          AND hl.block != ''
        GROUP BY hl.block
        """,
        {"start_date": start_date, "end_date": end_date},
        as_dict=True,
    )

    cost_map = {r.block: float(r.estimated_cost or 0) for r in cost_rows}

    result = []
    for row in harvest_rows:
        cherry_kg = float(row.total_cherry_kg or 0)
        cost = cost_map.get(row.block, 0.0)
        result.append(
            {
                "block": row.block,
                "harvest_days": int(row.harvest_days or 0),
                "total_buckets": int(row.total_buckets or 0),
                "total_cherry_kg": cherry_kg,
                "estimated_cost": round(cost),
                "cost_per_kg": round(cost / cherry_kg, 1) if cherry_kg > 0 else 0,
            }
        )
    return result
