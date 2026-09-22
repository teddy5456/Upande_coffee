import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class CoffeeForecastOverride(Document):
	def validate(self):
		# Weeks are Mondays everywhere in this module; snapping here means an
		# override entered mid-week still lands on the right column.
		d = getdate(self.week_start)
		self.week_start = frappe.utils.add_days(d, -d.weekday())

		dupe = frappe.db.exists("Coffee Forecast Override", {
			"block": self.block, "week_start": self.week_start,
			"name": ("!=", self.name or ""),
		})
		if dupe:
			frappe.throw(
				f"{self.block} already has an override for the week of {self.week_start}. "
				"Edit that one so the history stays in one place."
			)
