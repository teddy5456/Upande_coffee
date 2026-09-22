import frappe
from frappe.model.document import Document
from frappe.utils import flt


class CoffeeCherryWeightSample(Document):
	def validate(self):
		n = flt(self.cherries_counted)
		if n <= 0:
			frappe.throw("Count at least one cherry.")
		self.avg_cherry_weight_g = flt(self.total_weight_g) / n

		default = flt(frappe.get_cached_doc("Coffee Production Settings").avg_cherry_weight_g)
		self.vs_default_pct = (
			(self.avg_cherry_weight_g - default) / default * 100.0 if default else 0
		)
