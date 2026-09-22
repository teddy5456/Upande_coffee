import frappe
from frappe.model.document import Document
from frappe.utils import flt


class CoffeeBerrySample(Document):
	def validate(self):
		self.set_estimate()

	def set_estimate(self):
		settings = frappe.get_cached_doc("Coffee Production Settings")
		# Prefer, in order: a weight typed on this sample, the block's own most
		# recent Cherry Weight Sample, then the estate default. Cherry weight
		# swings with rainfall, so the block's own measurement beats a constant.
		measured = frappe.db.get_value(
			"Coffee Cherry Weight Sample",
			{"block": self.block},
			"avg_cherry_weight_g",
			order_by="sample_date desc",
		)
		grams = (flt(self.avg_cherry_weight_g) or flt(measured)
				 or flt(settings.avg_cherry_weight_g) or 1.5)

		# A coffee tree is counted the way it is built: primaries off the stem,
		# secondaries off the primaries, fruiting nodes along both. Counting only
		# "nodes per tree" works on a young block and loses badly on an old one,
		# where the secondaries carry most of the crop. Where the walker gave us
		# the structure, derive the node total from it; where they only gave a
		# node total (or the row predates this), keep what they typed.
		branches = flt(self.bearing_primaries) * (1.0 + flt(self.secondaries_per_primary))
		if branches and flt(self.nodes_per_branch):
			self.bearing_nodes_per_tree = branches * flt(self.nodes_per_branch)

		berries = flt(self.bearing_nodes_per_tree) * flt(self.berries_per_node)

		# Not every counted berry is picked. Abscission, CBD, borer and stripping
		# losses sit between the count and the store, and published Arabica
		# figures put that at 10-40% under water stress (24-30% where CBD runs
		# unchecked). Ignoring it is how a count that was taken honestly still
		# over-promises.
		if self.drop_pct in (None, ""):
			self.drop_pct = flt(settings.expected_drop_pct)
		keep = 1.0 - min(max(flt(self.drop_pct), 0.0), 90.0) / 100.0

		self.est_kg_per_tree = berries * grams / 1000.0 * keep

		# Scale by BEARING trees, not planted trees — gaps produce nothing, and
		# using the planted count is how estimates end up flattering.
		bearing = frappe.db.get_value("Coffee Block Profile", {"block": self.block}, "effective_trees")
		self.est_block_kg = flt(self.est_kg_per_tree) * flt(bearing or 0)

	def on_update(self):
		self.propagate()

	def propagate(self):
		"""A count is evidence about the whole variety, so push it outwards.

		Saving the protocol recomputes its measured plateau from every count;
		saving each block of that variety then re-scales its expectation. Done
		here rather than lazily at read time so the desk, the page and any report
		all see the same number without each recomputing it.
		"""
		variety = frappe.db.get_value("Coffee Block Profile", {"block": self.block},
									  "variety_protocol")
		if not variety:
			return
		protocol = frappe.get_doc("Coffee Variety Protocol", variety)
		protocol.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Coffee Variety Protocol")
		for name in frappe.get_all("Coffee Block Profile",
								   filters={"variety_protocol": variety}, pluck="name"):
			frappe.get_doc("Coffee Block Profile", name).save(ignore_permissions=True)
