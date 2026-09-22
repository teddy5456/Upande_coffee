import frappe
from frappe.model.document import Document
from frappe.utils import flt


class CoffeeCropLoss(Document):
	def validate(self):
		expected = flt(frappe.db.get_value(
			"Coffee Block Profile", {"block": self.block}, "expected_kg_cherry"))
		self.est_kg_lost = expected * flt(self.pct_of_crop) / 100.0
		price = flt(frappe.get_cached_doc("Coffee Production Settings").cherry_price_per_kg)
		self.est_value_lost = flt(self.est_kg_lost) * price
