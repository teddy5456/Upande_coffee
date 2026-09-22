import frappe
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

# Below this a shower wets the leaves and little else; a flowering flush needs a
# real soaking. A threshold, not a law of nature — tune it in the field.
TRIGGER_MM = 20.0
DAYS_TO_FLOWER = 8


class CoffeeRainfallLog(Document):
	def validate(self):
		self.triggers_flowering = 1 if flt(self.rainfall_mm) >= TRIGGER_MM else 0
		self.expected_flowering_date = (
			add_days(getdate(self.reading_date), DAYS_TO_FLOWER)
			if self.triggers_flowering else None
		)
