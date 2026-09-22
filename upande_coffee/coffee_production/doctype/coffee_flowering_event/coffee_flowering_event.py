import frappe
from frappe.model.document import Document
from frappe.utils import add_days, getdate

# What each intensity is worth relative to the block's expected crop. Deliberately
# coarse: a scout can tell light from heavy reliably, but not 55% from 60%.
INTENSITY_WEIGHT = {"Light": 0.4, "Moderate": 0.7, "Heavy": 1.0}


class CoffeeFloweringEvent(Document):
	def validate(self):
		self.set_window()

	def set_window(self):
		settings = frappe.get_cached_doc("Coffee Production Settings")
		lag_weeks = int(settings.ripening_weeks or 32)
		spread = int(settings.ripening_spread_weeks or 6)

		peak = add_days(getdate(self.flowering_date), lag_weeks * 7)
		self.expected_peak_date = peak
		# The spread straddles the peak, so half of it falls either side.
		half = max(spread // 2, 0) * 7
		self.expected_from_date = add_days(peak, -half)
		self.expected_to_date = add_days(peak, half)

	def weight(self):
		"""Share of the block's expected crop attributable to this flowering."""
		base = INTENSITY_WEIGHT.get(self.intensity, 0.7)
		return base * (float(self.pct_of_block or 100) / 100.0)
