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
            WHERE docstatus != 2
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

    # Per-block estimated yield from Block Yield Estimate. Prefer an estimate
    # whose season overlaps the requested range; fall back to a season-less
    # (default) estimate. Guarded so the API keeps working on instances that
    # haven't migrated the new doctype yet.
    est_map = {}
    try:
        est_rows = frappe.db.sql(
            """
            SELECT
                bye.block,
                bye.estimated_weight_kg,
                CASE
                    WHEN bye.season IS NULL OR bye.season = '' THEN 0
                    WHEN cs.start_date <= %(end_date)s AND cs.end_date >= %(start_date)s THEN 1
                    ELSE NULL
                END AS season_match
            FROM `tabBlock Yield Estimate` bye
            LEFT JOIN `tabCoffee Season` cs ON cs.name = bye.season
            HAVING season_match IS NOT NULL
            ORDER BY season_match ASC, bye.modified ASC
            """,
            {"start_date": start_date, "end_date": end_date},
            as_dict=True,
        )
        # season-matched rows sort last, so they overwrite season-less defaults
        for r in est_rows:
            est_map[r.block] = float(r.estimated_weight_kg or 0)
    except Exception:
        frappe.clear_last_message()

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
                "estimated_weight_kg": est_map.get(row.block, 0.0),
            }
        )

    # Blocks that have an estimate but no harvest in the range still show up,
    # so under-performing blocks are visible instead of silently missing.
    seen = {r["block"] for r in result}
    for block, est in est_map.items():
        if block not in seen and est > 0:
            result.append(
                {
                    "block": block,
                    "harvest_days": 0,
                    "total_buckets": 0,
                    "total_cherry_kg": 0.0,
                    "estimated_cost": 0,
                    "cost_per_kg": 0,
                    "estimated_weight_kg": est,
                }
            )
    return result
