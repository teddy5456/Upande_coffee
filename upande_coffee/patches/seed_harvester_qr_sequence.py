"""
Patch: seed_harvester_qr_sequence

The Coffee QR Sequence single now owns harvester numbering. Seed its counter
from the highest existing HARVESTER-n so newly created harvesters continue the
run instead of colliding with existing records.

Idempotent: only advances the counter, never lowers it.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "Coffee QR Sequence"):
        return

    current_max = (
        frappe.db.sql(
            """
            SELECT MAX(CAST(SUBSTRING(harvester_id, 11) AS UNSIGNED))
            FROM `tabHarvester`
            WHERE harvester_id LIKE 'HARVESTER-%'
            """
        )[0][0]
        or 0
    )

    seq = frappe.get_single("Coffee QR Sequence")
    if (seq.harvester_counter or 0) < current_max:
        seq.harvester_counter = current_max
        seq.save(ignore_permissions=True)
        frappe.db.commit()
