import json

import frappe
from frappe import _

TRACKED_FIELDS = {"bucket_count", "weight_kg"}
BLOCK_PICKUPS_FIELDNAME = "block_pickups"


def execute(filters=None):
    columns = get_columns()
    data = get_data()
    return columns, data


def get_columns():
    return [
        {"label": _("Pickup"), "fieldname": "pickup", "fieldtype": "Link", "options": "Harvest Pickup", "width": 140},
        {"label": _("Pickup Date"), "fieldname": "pickup_date", "fieldtype": "Date", "width": 100},
        {"label": _("Block"), "fieldname": "block", "fieldtype": "Link", "options": "Warehouse", "width": 200},
        {"label": _("Field"), "fieldname": "field", "fieldtype": "Data", "width": 110},
        {"label": _("Original"), "fieldname": "original_value", "fieldtype": "Data", "width": 100},
        {"label": _("Corrected"), "fieldname": "corrected_value", "fieldtype": "Data", "width": 100},
        {"label": _("Corrected By"), "fieldname": "corrected_by", "fieldtype": "Link", "options": "User", "width": 160},
        {"label": _("Corrected On"), "fieldname": "corrected_on", "fieldtype": "Datetime", "width": 160},
    ]


def get_data():
    versions = frappe.get_all(
        "Version",
        filters={"ref_doctype": "Harvest Pickup"},
        fields=["docname", "data", "owner", "creation"],
        order_by="creation desc",
        limit_page_length=0,
    )

    pickup_date_cache = {}
    row_block_cache = {}
    rows = []
    for v in versions:
        for correction in _extract_corrections(v.data):
            row_name = correction["row_name"]
            if row_name not in row_block_cache:
                row_block_cache[row_name] = frappe.db.get_value(
                    "Harvest Pickup Detail", row_name, "block"
                )
            if v.docname not in pickup_date_cache:
                pickup_date_cache[v.docname] = frappe.db.get_value(
                    "Harvest Pickup", v.docname, "date"
                )
            rows.append(
                {
                    "pickup": v.docname,
                    "pickup_date": pickup_date_cache[v.docname],
                    "block": row_block_cache[row_name],
                    "field": correction["field"],
                    "original_value": correction["old_value"],
                    "corrected_value": correction["new_value"],
                    "corrected_by": v.owner,
                    "corrected_on": v.creation,
                }
            )
    return rows


def _extract_corrections(version_data):
    """Pull bucket_count/weight_kg row changes out of a Version.data blob.

    Version stores row_changed as [table_fieldname, row_index, row_name,
    [[fieldname, old, new], ...]] (see frappe.core.doctype.version.version.get_diff).
    """
    try:
        payload = json.loads(version_data or "{}")
    except ValueError:
        return []

    out = []
    for row_change in payload.get("row_changed", []):
        if len(row_change) < 4 or row_change[0] != BLOCK_PICKUPS_FIELDNAME:
            continue
        row_name, field_changes = row_change[2], row_change[3]
        for field_change in field_changes:
            if len(field_change) < 3:
                continue
            fieldname, old_value, new_value = field_change[0], field_change[1], field_change[2]
            if fieldname in TRACKED_FIELDS:
                out.append(
                    {
                        "row_name": row_name,
                        "field": fieldname,
                        "old_value": old_value,
                        "new_value": new_value,
                    }
                )
    return out


def _selftest():
    sample = json.dumps(
        {
            "row_changed": [
                ["block_pickups", 0, "hpd-row-1", [["weight_kg", 100, 120], ["available_buckets", 5, 5]]],
                ["some_other_table", 0, "other-row", [["qty", 1, 2]]],
            ]
        }
    )
    result = _extract_corrections(sample)
    assert result == [
        {"row_name": "hpd-row-1", "field": "weight_kg", "old_value": 100, "new_value": 120}
    ], result
    assert _extract_corrections(None) == []
    assert _extract_corrections("not json") == []
    print("bucket_correction_report self-test passed")


if __name__ == "__main__":
    _selftest()
