import frappe
from frappe import _
from frappe.model.document import Document


class BlockYieldEstimate(Document):
    def validate(self):
        # One estimate per block per season (season may be blank for a
        # season-agnostic default estimate).
        filters = {
            "block": self.block,
            "season": self.season or "",
            "name": ("!=", self.name),
        }
        if frappe.db.exists("Block Yield Estimate", filters):
            frappe.throw(
                _("An estimate for block {0} in this season already exists.").format(self.block)
            )
