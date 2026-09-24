import frappe
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate


class CoffeeProductionForecast(Document):
	def validate(self):
		# Weeks are Mondays everywhere in this module; snapping here means a
		# row entered mid-week still lands on the right column.
		d = getdate(self.week_start)
		self.week_start = add_days(d, -d.weekday())

		dupe = frappe.db.exists("Coffee Production Forecast", {
			"block": self.block, "week_start": self.week_start,
			"name": ("!=", self.name or ""),
		})
		if dupe:
			frappe.throw(
				f"{self.block} already has a forecast for the week of {self.week_start}. "
				"Edit that one so the history stays in one place."
			)

		self.actual_kg = self._weighbridge_kg()

		rate = flt(self.kg_per_worker) or flt(
			frappe.get_cached_doc("Coffee Production Settings").default_kg_per_worker
		)
		self.computed_workers_needed = round(flt(self.revised_forecast_kg) / rate, 1) if rate else 0.0

	def _weighbridge_kg(self):
		# Same weighbridge query the /coffee-production grid uses for its actual
		# column (productiongrid._actual_map) — one source of truth for "actual
		# kg for a block in a week" rather than a second copy of the SQL.
		from upande_coffee.api.productiongrid import _actual_map, _iso

		pair = _iso(self.week_start)
		return flt(_actual_map([pair]).get((self.block, pair)))
