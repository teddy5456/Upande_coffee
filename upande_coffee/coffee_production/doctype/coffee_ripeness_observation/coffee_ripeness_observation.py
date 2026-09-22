import frappe
from frappe.model.document import Document
from frappe.utils import flt


class CoffeeRipenessObservation(Document):
	def validate(self):
		parts = [flt(self.pct_unripe), flt(self.pct_ripe),
			 flt(self.pct_overripe), flt(self.pct_dropped)]
		total = sum(parts)
		if total > 130:
			frappe.throw(f"The percentages add up to {total:.0f}%. Check them.")
		if not total:
			frappe.throw("Record at least one percentage.")

		self.readiness = self._readiness()
		self.quality_risk = self._risk()

	def _readiness(self):
		"""Ripe cherry is the thing worth sending pickers for.

		The thresholds are deliberately coarse — a scout can judge 'about a third
		red' reliably, and pretending to more precision than that would be false.
		"""
		ripe = flt(self.pct_ripe)
		over = flt(self.pct_overripe) + flt(self.pct_dropped)
		if over >= 15 or (ripe >= 50 and over >= 10):
			return "Overdue"
		if ripe >= 30:
			return "Pick Now"
		if ripe >= 12:
			return "Approaching"
		return "Not Ready"

	def _risk(self):
		spoil = flt(self.pct_overripe) + flt(self.pct_dropped)
		if spoil >= 15:
			return "High"
		if spoil >= 6:
			return "Medium"
		return "Low"
