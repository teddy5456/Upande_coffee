import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class CoffeeSeason(Document):
    def validate(self):
        self._validate_date_range()
        self._enforce_single_active()

    def _validate_date_range(self):
        if self.start_date and self.end_date:
            if getdate(self.end_date) < getdate(self.start_date):
                frappe.throw(_("End Date cannot be before Start Date."))

    def _enforce_single_active(self):
        """Only one Coffee Season can be flagged Active at a time."""
        if not self.is_active:
            return
        others = frappe.get_all(
            "Coffee Season",
            filters={"is_active": 1, "name": ["!=", self.name]},
            pluck="name",
        )
        for other in others:
            frappe.db.set_value("Coffee Season", other, "is_active", 0, update_modified=False)
            frappe.msgprint(
                _("Deactivated previously active season: {0}").format(other),
                indicator="orange",
                alert=True,
            )


@frappe.whitelist()
def get_active_season():
    """Return the currently active Coffee Season as a dict, or None.

    Used by the Kahawa Trail mobile app to default the bucket rate, scope
    reports to the season window, and show a season banner on the dashboard.
    """
    rows = frappe.get_all(
        "Coffee Season",
        filters={"is_active": 1},
        fields=[
            "name",
            "season_name",
            "start_date",
            "end_date",
            "default_bucket_rate",
            "target_cherry_kg",
            "cafe_certified",
            "ra_certified",
            "notes",
        ],
        limit=1,
    )
    return rows[0] if rows else None
