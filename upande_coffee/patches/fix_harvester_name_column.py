"""
Patch: fix_harvester_name_column

Harvester originally used `autoname: autoincrement`, which makes `tabHarvester.name`
an integer column. The doctype now uses `autoname: field:harvester_id` (values like
"HARVESTER-42"), but Frappe does not auto-convert the physical column type, so inserts
fail with "Incorrect integer value ... for column name". Convert `name` (and the
`parent`/child ref columns are irrelevant here) to varchar so string names insert.

Idempotent: only alters when the column is still an integer type.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Harvester"):
        return

    col = frappe.db.sql(
        """
        SELECT DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'tabHarvester' AND COLUMN_NAME = 'name'
        """,
        (frappe.conf.db_name,),
        as_dict=True,
    )
    if not col:
        return

    data_type = (col[0].get("DATA_TYPE") or "").lower()
    if data_type in ("varchar", "char", "text"):
        return  # already a string column, nothing to do

    frappe.db.sql("ALTER TABLE `tabHarvester` MODIFY `name` VARCHAR(140)")
    frappe.db.commit()
