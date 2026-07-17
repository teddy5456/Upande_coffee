import frappe
from frappe.model.document import Document


class Harvester(Document):
    def before_insert(self):
        if not self.harvester_id:
            # Auto-generate harvester ID: HARVESTER-XXXX
            last = frappe.db.sql(
                "SELECT MAX(CAST(SUBSTRING(harvester_id, 11) AS UNSIGNED)) FROM `tabHarvester` WHERE harvester_id LIKE 'HARVESTER-%'",
                as_list=True,
            )
            next_num = (last[0][0] or 0) + 1
            self.harvester_id = f"HARVESTER-{next_num}"

    def after_save(self):
        self._render_qr()

    def _render_qr(self):
        if not self.harvester_id:
            return
        import json
        qr_data = json.dumps({"harvester_id": self.harvester_id})
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?data={frappe.utils.quote(qr_data)}&size=200x200"
        html = f"""
        <div style="display:flex;flex-direction:column;align-items:center;padding:16px;
                    background:#fdf6ee;border-radius:12px;border:1px solid #d7a96b;text-align:center;">
            <h4 style="color:#4E342E;margin-bottom:8px;">Harvester QR Code</h4>
            <img src="{qr_url}" style="width:200px;height:200px;border-radius:6px;background:#fff;padding:8px;border:1px solid #ccc;">
            <p style="margin-top:10px;color:#6D4C41;font-size:13px;"><b>{self.harvester_id}</b></p>
        </div>
        """
        frappe.db.set_value("Harvester", self.name, "qr_display", html, update_modified=False)
