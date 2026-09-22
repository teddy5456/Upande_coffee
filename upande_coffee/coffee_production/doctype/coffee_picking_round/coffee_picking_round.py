import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate


class CoffeePickingRound(Document):
	def validate(self):
		if self.end_date and getdate(self.end_date) < getdate(self.start_date):
			frappe.throw("The round ends before it starts.")
		self.pull_actuals()

	def pull_actuals(self):
		"""Read the result out of existing records rather than asking for a total.

		Two sources, because the estate measures two different things:
		  - buckets come from Harvest Log, counted per picker as they scan
		  - kilograms come from Harvest Pickup Detail, the weighbridge weight per
		    block once the tractor is weighed in

		There is no bucket-to-kg constant anywhere and there should not be: bucket
		fill varies, and the weighbridge is the number the estate is paid on. A
		hand-typed total would only ever disagree with both.
		"""
		if not (self.block and self.start_date):
			return
		end = self.end_date or self.start_date

		picked = frappe.db.sql(
			"""SELECT COALESCE(SUM(bucket_count), 0) AS buckets,
				COUNT(DISTINCT harvester_id) AS pickers,
				COUNT(DISTINCT date) AS days
			FROM `tabHarvest Log`
			WHERE block = %(b)s AND date BETWEEN %(f)s AND %(t)s AND docstatus < 2""",
			{"b": self.block, "f": self.start_date, "t": end},
			as_dict=True,
		)[0]

		# Only submitted pickups count — a draft has not been weighed yet.
		weighed = frappe.db.sql(
			"""SELECT COALESCE(SUM(hpd.weight_kg), 0) AS kg
			FROM `tabHarvest Pickup Detail` hpd
			JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
			WHERE hpd.block = %(b)s AND hp.date BETWEEN %(f)s AND %(t)s
			  AND hp.docstatus = 1""",
			{"b": self.block, "f": self.start_date, "t": end},
			as_dict=True,
		)[0]

		self.buckets = int(picked.buckets or 0)
		self.pickers = int(picked.pickers or 0)
		self.cherry_kg = flt(weighed.kg)

		picker_days = flt(picked.pickers) * flt(picked.days)
		self.kg_per_picker_day = (flt(self.cherry_kg) / picker_days) if picker_days else 0
