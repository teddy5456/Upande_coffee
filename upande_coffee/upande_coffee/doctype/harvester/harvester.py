import json

import frappe
from frappe import _
from frappe.model.document import Document


def qr_url_for(harvester_id):
    """External QR image URL for a harvester — shared with label_generation.py
    so bulk label printing never has to render/store a QR PNG locally."""
    qr_data = json.dumps({"harvester_id": harvester_id})
    return f"https://api.qrserver.com/v1/create-qr-code/?data={frappe.utils.quote(qr_data)}&size=200x200"


class Harvester(Document):
    def before_insert(self):
        if not self.harvester_id:
            # Pull the next number from the central Coffee QR Sequence so records
            # created directly and via the label tool never collide.
            base = frappe.get_single("Coffee QR Sequence").get_next(1)
            self.harvester_id = f"HARVESTER-{base + 1}"

    def validate(self):
        self._pull_employee_details()
        self._one_harvester_per_employee()

    def _one_harvester_per_employee(self):
        """An employee carries one QR identity, so refuse a second Harvester
        for them instead of quietly printing two cards for one picker."""
        if not self.employee:
            return
        existing = frappe.db.get_value(
            "Harvester", {"employee": self.employee, "name": ["!=", self.name]}, "name"
        )
        if existing:
            frappe.throw(
                _("Employee {0} already has harvester {1}.").format(self.employee, existing),
                title=_("Duplicate Harvester"),
            )

    def _pull_employee_details(self):
        """Copy identifying details off the linked Employee.

        Lives here rather than in the label tool so picking an employee gives
        a fully populated Harvester however the record is created — label
        print, desk form or import.
        """
        if not self.employee:
            return

        emp = frappe.db.get_value(
            "Employee",
            self.employee,
            ["employee_number", "employee_name"],
            as_dict=True,
        )
        if not emp:
            return

        if not self.employee_id:
            self.employee_id = emp.employee_number

        if not self.national_id:
            # National ID has no fixed fieldname across Upande sites; take the
            # first one this site actually has rather than assuming.
            meta = frappe.get_meta("Employee")
            for candidate in ("custom_national_id", "national_id", "custom_id_number", "ic_no"):
                if meta.has_field(candidate):
                    value = frappe.db.get_value("Employee", self.employee, candidate)
                    if value:
                        self.national_id = value
                        break

    def after_save(self):
        self._render_qr()

    def _render_qr(self):
        if not self.harvester_id:
            return
        qr_url = qr_url_for(self.harvester_id)
        html = f"""
        <div style="display:flex;flex-direction:column;align-items:center;padding:16px;
                    background:#fdf6ee;border-radius:12px;border:1px solid #d7a96b;text-align:center;">
            <h4 style="color:#4E342E;margin-bottom:8px;">Harvester QR Code</h4>
            <img src="{qr_url}" style="width:200px;height:200px;border-radius:6px;background:#fff;padding:8px;border:1px solid #ccc;">
            <p style="margin-top:10px;color:#6D4C41;font-size:13px;"><b>{self.harvester_id}</b></p>
        </div>
        """
        frappe.db.set_value("Harvester", self.name, "qr_display", html, update_modified=False)
