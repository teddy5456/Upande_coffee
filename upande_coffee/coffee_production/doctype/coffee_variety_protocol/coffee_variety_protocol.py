import frappe
from frappe.model.document import Document
from frappe.utils import flt

from upande_coffee.coffee_production.actuals import block_actuals, window_label

# Fallback curve when a protocol carries no explicit stages. Coffee gives nothing
# for two years, first crop in year 3, full bearing 5-15, then declines. These are
# typical Kenyan arabica shapes — override per variety with real history.
# Standard change-of-cycle shape: nothing while young, a light first crop after
# stumping, peaking in cycle year 3-4, tailing off by 5-6 when the block is due to
# be stumped again. These are conventional Kenyan figures and want confirming
# against the estate's own block history.
DEFAULT_CYCLE_CURVE = {
	"YOUNG": 0,
	"0-1": 10,
	"1-2": 45,
	"2-3": 85,
	"3-4": 100,
	"4-5": 90,
	"5-6": 70,
}

DEFAULT_CURVE = [
	(0, 2, 0),
	(3, 3, 30),
	(4, 4, 65),
	(5, 15, 100),
	(16, 20, 85),
	(21, 30, 60),
	(31, 99, 35),
]


class CoffeeVarietyProtocol(Document):
	def validate(self):
		self._validate_bands()
		self.refresh_measured_plateau()
		self.refresh_history_plateau()

	def refresh_measured_plateau(self):
		"""Back out the variety's full-bearing yield from actual berry counts.

		A berry count measures kg per tree on one block, and that block sits
		somewhere on the pruning-cycle curve — a count taken in cycle year 2-3 is
		of a block yielding about 85% of its potential. Dividing the count by that
		share recovers what the variety does at full bearing, which is the number
		every other block of the variety is then scaled from.

		Counts on YOUNG blocks are skipped: dividing by zero potential says
		nothing, and a young block's yield is not evidence about a mature one.
		"""
		rows = frappe.db.sql(
			"""SELECT bs.est_kg_per_tree, bs.trees_sampled, bs.sample_date,
				bp.cycle_year, bp.tree_age, bp.block
			FROM `tabCoffee Berry Sample` bs
			JOIN `tabCoffee Block Profile` bp ON bp.block = bs.block
			WHERE bp.variety_protocol = %(v)s AND bs.est_kg_per_tree > 0
			ORDER BY bs.sample_date DESC""",
			{"v": self.name},
			as_dict=True,
		)

		# One count per block — the most recent. Older counts on the same block
		# are history, not extra evidence.
		seen, weighted, weight_total = set(), 0.0, 0.0
		used = 0
		for r in rows:
			if r.block in seen:
				continue
			seen.add(r.block)
			pct = self.pct_at_cycle(r.cycle_year)
			if pct is None:
				pct = self.pct_at_age(r.tree_age)
			if not pct:
				continue
			implied = flt(r.est_kg_per_tree) / (flt(pct) / 100.0)
			# Weight by trees sampled: ten trees is better evidence than three.
			w = max(flt(r.trees_sampled), 1.0)
			weighted += implied * w
			weight_total += w
			used += 1

		if used:
			self.measured_plateau_kg = weighted / weight_total
			self.plateau_sample_count = used
			self.plateau_basis = "measured"
		else:
			self.measured_plateau_kg = 0
			self.plateau_sample_count = 0
			self.plateau_basis = "typed"

	def refresh_history_plateau(self):
		"""Back the plateau out of what was actually weighed in.

		Same arithmetic as the berry counts above, on better evidence: a season of
		receipts for a block, divided by that block's share of plateau, says what
		the variety does at full bearing. A count samples ten trees on one day; the
		weighbridge saw every bucket.
		"""
		rows = frappe.get_all(
			"Coffee Block Profile",
			filters={"variety_protocol": self.name},
			fields=["block", "cycle_year", "tree_age", "status", "effective_trees"],
			limit_page_length=0,
		)
		actuals = block_actuals()

		weighted, weight_total, used = 0.0, 0.0, 0
		for r in rows:
			if r.status in ("Stumped", "Abandoned"):
				continue
			a = actuals.get(r.block)
			if not a or not flt(a["kg_per_tree"]):
				continue
			pct = self.pct_at_cycle(r.cycle_year)
			if pct is None:
				pct = self.pct_at_age(r.tree_age)
			if not pct:
				continue
			# Weight by trees: a 30,000-tree block is stronger evidence than a
			# 200-tree one, and it is also the block that decides the estate total.
			w = max(flt(r.effective_trees), 1.0)
			weighted += (flt(a["kg_per_tree"]) / (flt(pct) / 100.0)) * w
			weight_total += w
			used += 1

		self.history_plateau_kg = (weighted / weight_total) if weight_total else 0
		self.history_blocks = used
		self.history_window = window_label() if used else ""
		if used:
			self.plateau_basis = "actual receipts"

	def plateau(self):
		"""kg cherry per tree at full bearing.

		Receipts beat counts beat a typed guess. Nothing here is invented: the
		typed figure is only reached by a variety with no weighbridge history and
		no berry count anywhere, and the basis is reported alongside every number
		so a guess can never pass for a measurement.
		"""
		if flt(self.history_plateau_kg):
			return flt(self.history_plateau_kg)
		if flt(self.measured_plateau_kg):
			return flt(self.measured_plateau_kg)
		return flt(self.plateau_kg_cherry_per_tree)

	def _validate_bands(self):
		"""Bands must not overlap, or a tree's age would match two yields and the
		forecast would silently depend on row order."""
		bands = sorted(
			[(int(r.age_from or 0), int(r.age_to or 0), r.idx) for r in self.yield_stages]
		)
		for i, (lo, hi, idx) in enumerate(bands):
			if hi < lo:
				frappe.throw(f"Row {idx}: tree age {lo}-{hi} ends before it starts.")
			if i and lo <= bands[i - 1][1]:
				frappe.throw(
					f"Row {idx}: tree ages {lo}-{hi} overlap the previous band "
					f"{bands[i - 1][0]}-{bands[i - 1][1]}."
				)

	def curve(self):
		"""[(age_from, age_to, pct)] — explicit stages if given, else the default."""
		if self.yield_stages:
			return [
				(int(r.age_from or 0), int(r.age_to or 0), flt(r.pct_of_plateau))
				for r in self.yield_stages
			]
		return [(a, b, float(p)) for a, b, p in DEFAULT_CURVE]

	def cycle_curve(self):
		"""{cycle band: pct} — explicit rows if given, else the standard shape."""
		if self.cycle_stages:
			return {r.cycle_year: flt(r.pct_of_plateau) for r in self.cycle_stages if r.cycle_year}
		return dict(DEFAULT_CYCLE_CURVE)

	def pct_at_cycle(self, cycle_year):
		"""Share of plateau yield for a block this many years since stumping.

		This is the right axis for a cycle-pruned estate: a thirty-year-old block
		stumped two years ago yields like a young one, and tree age would promise
		a full crop it cannot deliver.
		"""
		if not cycle_year:
			return None
		return self.cycle_curve().get(str(cycle_year).strip())

	def pct_at_age(self, age_years):
		"""Share of plateau yield expected at this tree age, 0 if past the curve."""
		if age_years is None or age_years < 0:
			return 0.0
		for lo, hi, pct in self.curve():
			if lo <= age_years <= hi:
				return pct
		return 0.0

	def kg_per_tree_at_age(self, age_years):
		return self.plateau() * self.pct_at_age(age_years) / 100.0

	def kg_per_tree(self, cycle_year=None, age_years=None):
		"""Per-tree yield, preferring the pruning cycle when it is known."""
		pct = self.pct_at_cycle(cycle_year)
		if pct is None:
			pct = self.pct_at_age(age_years)
		return self.plateau() * flt(pct) / 100.0
