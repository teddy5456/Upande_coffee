# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Generate harvester QR label cards for a Harvester Label Print document.

QR images are an external api.qrserver.com URL (same one Harvester's own
qr_display uses) rather than a locally rendered PNG + File record — a batch of
a few hundred cards used to time out the web request doing that render/write
per harvester; a URL is free to build and needs no file I/O at all.
"""

import frappe
from frappe import _

from upande_coffee.upande_coffee.doctype.harvester.harvester import qr_url_for


def _harvester_for_employee(employee):
    """Return the employee's Harvester, creating and linking one if needed.

    Employee number and national ID are stamped by the Harvester controller,
    so the record that comes back is fully identified either way.
    """
    name = frappe.db.get_value("Harvester", {"employee": employee}, "name")
    if name:
        return frappe.get_doc("Harvester", name)
    doc = frappe.get_doc({"doctype": "Harvester", "employee": employee})
    doc.insert(ignore_permissions=True)
    return doc


def _resolve_harvesters(doc):
    """Return the list of Harvester docs to print for the chosen mode."""
    action = doc.action

    if action == "Empty QR Range":
        qty = int(doc.qty or 0)
        if qty < 1:
            frappe.throw(_("Enter a Number of Cards greater than zero."))
        # Reserve the whole block in one shot: Coffee QR Sequence.get_next(n)
        # takes a single row lock and commit for the batch, instead of one
        # per harvester (which is what made large ranges slow — each of
        # those is a serialized SELECT ... FOR UPDATE plus its own commit).
        base = frappe.get_single("Coffee QR Sequence").get_next(qty)
        harvesters = []
        for i in range(qty):
            h = frappe.get_doc({"doctype": "Harvester", "harvester_id": f"HARVESTER-{base + 1 + i}"})
            h.insert(ignore_permissions=True)
            harvesters.append(h)
        return harvesters

    if action == "Employee Card":
        if not doc.employee:
            frappe.throw(_("Select an Employee."))
        return [_harvester_for_employee(doc.employee)]

    if action == "Employee Table":
        if not doc.employee_rows:
            frappe.throw(_("Add at least one employee to the table."))
        seen = set()
        harvesters = []
        for row in doc.employee_rows:
            if not row.employee or row.employee in seen:
                continue
            seen.add(row.employee)
            harvesters.append(_harvester_for_employee(row.employee))
        if not harvesters:
            frappe.throw(_("No valid employees in the table."))
        return harvesters

    if action == "Reprint Existing":
        # Reprints a label for a harvester that already exists — nothing is
        # created. Useful for a lost/damaged physical card.
        if not doc.existing_harvester:
            frappe.throw(_("Select a Harvester to reprint."))
        return [frappe.get_doc("Harvester", doc.existing_harvester)]

    frappe.throw(_("Unknown mode: {0}").format(action))


# Past this many, a batch risks outrunning the web request timeout, so it
# moves to a background job instead. QR images are now free (external URL,
# no local render/file write), so the only per-harvester cost left is the
# doc insert itself — this can sit much higher than it could when each one
# also wrote a PNG to disk.
BACKGROUND_THRESHOLD = 300


def _expected_count(doc):
    if doc.action == "Empty QR Range":
        return int(doc.qty or 0)
    if doc.action == "Employee Table":
        return len({row.employee for row in (doc.employee_rows or []) if row.employee})
    return 1


@frappe.whitelist()
def generate_labels(label_doc_name):
    """Create/link Harvester records for the chosen mode and build label rows.

    Large batches (bulk "Empty QR Range" runs especially) are enqueued instead
    of run inline — see BACKGROUND_THRESHOLD."""
    doc = frappe.get_doc("Harvester Label Print", label_doc_name)
    doc.check_permission("write")

    if _expected_count(doc) > BACKGROUND_THRESHOLD:
        frappe.enqueue(
            "upande_coffee.upande_coffee.label_generation._generate_labels_job",
            queue="long",
            timeout=3600,
            label_doc_name=label_doc_name,
            user=frappe.session.user,
        )
        return {"queued": True}

    count = _generate_labels_job(label_doc_name)
    return {"count": count, "queued": False}


def _generate_labels_job(label_doc_name, user=None):
    """Does the actual work of generate_labels — runs either inline or as a
    background job. `user` is who to notify when queued; inline calls don't
    need it since the caller is still waiting on the response."""
    doc = frappe.get_doc("Harvester Label Print", label_doc_name)
    harvesters = _resolve_harvesters(doc)

    doc.set("labels", [])
    for h in harvesters:
        emp_name = emp_number = None
        if h.employee:
            emp_name, emp_number = frappe.db.get_value(
                "Employee", h.employee, ["employee_name", "employee_number"]
            ) or (None, None)

        doc.append(
            "labels",
            {
                "harvester_id": h.harvester_id,
                "employee": h.employee,
                "employee_name": emp_name,
                "employee_number": emp_number,
                "qr_code_image": qr_url_for(h.harvester_id),
            },
        )

    doc.flags.ignore_validate_update_after_submit = True
    doc.save(ignore_permissions=bool(user))
    frappe.db.commit()

    if user:
        frappe.publish_realtime(
            "harvester_label_print_done",
            {"name": label_doc_name, "count": len(doc.labels)},
            user=user,
        )
    return len(doc.labels)
