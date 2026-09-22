import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from upande_coffee.coffee_production.actuals import block_actuals


class CoffeeBlockProfile(Document):
	def validate(self):
		self.set_census()
		self.set_actuals()
		self.set_expected_yield()

	def set_actuals(self):
		"""What this block delivered over the last season of picking."""
		a = block_actuals().get(self.block) or {}
		self.actual_kg_per_tree = flt(a.get("kg_per_tree"))
		self.actual_kg_cherry = flt(a.get("kg"))
		self.actual_window = (f"{a.get('from_date')} to {a.get('to_date')}"
							  if a.get("from_date") else "")

	def set_census(self):
		# Year planted is optional once a cycle year is known: the census records
		# the cycle, and many blocks predate anyone's memory of a planting date.
		if self.year_planted:
			self.tree_age = getdate(nowdate()).year - int(self.year_planted)
			if self.tree_age < 0:
				frappe.throw("Year planted is in the future.")
		planted = int(self.tree_count or 0)
		gaps = int(self.gap_count or 0)
		if gaps > planted:
			frappe.throw(f"Gaps ({gaps}) cannot exceed trees planted ({planted}).")
		self.effective_trees = planted - gaps
		self.gap_pct = (gaps / planted * 100.0) if planted else 0

	def set_expected_yield(self):
		"""Baseline expectation from the variety curve.

		A stumped or abandoned block is zeroed outright: the tree age would
		otherwise suggest a full crop that physically cannot arrive.
		"""
		if not self.variety_protocol or self.status in ("Stumped", "Abandoned"):
			self.expected_kg_per_tree = 0
			self.expected_kg_cherry = 0
			self.yield_basis = "not bearing"
			return
		# Best evidence first:
		#   1. this block's own berry count — a measurement of THIS block, at its
		#      current point in the cycle, needing no curve at all
		#   2. the variety's plateau (itself derived from counts elsewhere) scaled
		#      to this block's cycle year
		own = frappe.db.get_value(
			"Coffee Berry Sample", {"block": self.block}, "est_kg_per_tree",
			order_by="sample_date desc",
		)
		if flt(own):
			per_tree = flt(own)
			self.yield_basis = "own berry count"
		elif flt(self.actual_kg_per_tree):
			# ponytail: taken as-is, not re-scaled for the cycle year having moved
			# on since. Worth doing when a block's cycle year is stamped per
			# season; today it would be guessing which year the receipts sat in.
			per_tree = flt(self.actual_kg_per_tree)
			self.yield_basis = "own receipts"
		else:
			protocol = frappe.get_cached_doc("Coffee Variety Protocol", self.variety_protocol)
			per_tree = protocol.kg_per_tree(cycle_year=self.cycle_year, age_years=self.tree_age)
			self.yield_basis = (
				"variety counts" if protocol.plateau_basis == "measured" else "typed estimate"
			)
		self.expected_kg_per_tree = per_tree
		self.expected_kg_cherry = per_tree * flt(self.effective_trees)
