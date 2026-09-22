# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Generate harvester QR label cards for a Harvester Label Print document.

Mirrors the ``upande_kaitet`` ``gen_label_id`` convention: build a QR PNG with the
``qrcode`` library, store it as a File, and record a child row that the print
formats iterate over.
"""

import json
import os
import time

import qrcode

import frappe
from frappe import _


def _make_qr(harvester_id, label_doc_name, index):
    """Render the QR PNG for one harvester and return its public file URL."""
    payload = json.dumps({"harvester_id": harvester_id})

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    qr_dir = frappe.utils.get_files_path("qr_codes")
    os.makedirs(qr_dir, exist_ok=True)

    file_name = f"{label_doc_name}_{int(time.time())}_{index}.png"
    img.save(os.path.join(qr_dir, file_name))

    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_url": f"/files/qr_codes/{file_name}",
            "attached_to_doctype": "Harvester Label Print",
            "attached_to_name": label_doc_name,
            "is_private": 0,
        }
    )
    file_doc.insert(ignore_permissions=True)
    return file_doc.file_url


def _harvester_for_employee(employee):
    """Return an existing Harvester for the employee, creating one if needed."""
    name = frappe.db.get_value("Harvester", {"employee": employee}, "name")
    if name:
        return frappe.get_doc("Harvester", name)
    doc = frappe.get_doc({"doctype": "Harvester", "employee": employee})
    doc.insert()
    return doc


def _resolve_harvesters(doc):
    """Return the list of Harvester docs to print for the chosen mode."""
    action = doc.action

    if action == "Empty QR Range":
        qty = int(doc.qty or 0)
        if qty < 1:
            frappe.throw(_("Enter a Number of Cards greater than zero."))
        harvesters = []
        for _i in range(qty):
            h = frappe.get_doc({"doctype": "Harvester"})
            h.insert()
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

    frappe.throw(_("Unknown mode: {0}").format(action))


@frappe.whitelist()
def generate_labels(label_doc_name):
    """Create/link Harvester records for the chosen mode and build label rows."""
    doc = frappe.get_doc("Harvester Label Print", label_doc_name)
    doc.check_permission("write")

    harvesters = _resolve_harvesters(doc)

    doc.set("labels", [])
    for index, h in enumerate(harvesters, start=1):
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
                "qr_code_image": _make_qr(h.harvester_id, label_doc_name, index),
            },
        )

    doc.flags.ignore_validate_update_after_submit = True
    doc.save()
    frappe.db.commit()
    return {"count": len(doc.labels)}
