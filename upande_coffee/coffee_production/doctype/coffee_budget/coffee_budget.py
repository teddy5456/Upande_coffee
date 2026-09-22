import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CoffeeBudget(Document):
	def validate(self):
		self._check_duplicate_blocks()
		self.total_target_kg = sum(flt(row.target_kg) for row in self.blocks)

	def _check_duplicate_blocks(self):
		seen = set()
		for row in self.blocks:
			if row.block in seen:
				frappe.throw(_("Block {0} appears more than once — combine it into one row.").format(row.block))
			seen.add(row.block)
