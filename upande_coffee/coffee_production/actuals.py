# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

# What each block ACTUALLY delivered, per bearing tree.
#
# This exists because a forecast built on a typed plateau yield is a guess, and a
# guess was quietly running the whole module: 4.0 kg cherry per tree at full
# bearing, scaled down the cycle curve, came out at 2.74 kg/tree across the
# estate and forecast 1,123 t — against a crop that is physically about double
# that. The estate has known the right answer all along; it is in the weighbridge
# tickets. So the curve is now calibrated from receipts, and the typed number is
# only ever a last resort for a block with no history at all.
#
# Two sources, reconciled rather than picked between:
#   Harvest Pickup Detail  weighbridge kg per block  — the truth
#   Harvest Log            buckets per block         — complete, but in buckets
# Weighed buckets give the kg-per-bucket rate; any buckets logged beyond what was
# weighed are converted at that rate instead of being counted as nothing.

import frappe
from frappe.utils import add_days, flt, getdate

CACHE_KEY = "_coffee_actuals"


def block_actuals(months=12, refresh=False):
	"""{block: {kg, buckets, kg_per_tree, trees, weighed_kg, from_date, to_date}}

	Cached for the request: a single Block Profile save cascade re-reads this once
	per block otherwise, and the query spans a season of harvest logs.
	"""
	cached = getattr(frappe.local, CACHE_KEY, None)
	if cached is not None and not refresh:
		return cached

	# Anchor on the last pickup rather than today: mid-season, "the last twelve
	# months" and "the last twelve months of picking" are different windows, and
	# out of season today's date would slide the window off the crop entirely.
	last = frappe.db.sql("""SELECT MAX(date) FROM `tabHarvest Pickup` WHERE docstatus = 1""")
	to_date = getdate(last[0][0]) if last and last[0][0] else getdate()
	from_date = add_days(to_date, -int(round(30.44 * months)))

	weighed = frappe.db.sql(
		"""SELECT hpd.block AS block, SUM(hpd.weight_kg) AS kg, SUM(hpd.bucket_count) AS buckets
		FROM `tabHarvest Pickup Detail` hpd
		JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
		WHERE hp.docstatus = 1 AND hp.date BETWEEN %(f)s AND %(t)s
		GROUP BY hpd.block""",
		{"f": from_date, "t": to_date},
		as_dict=True,
	)
	logged = frappe.db.sql(
		"""SELECT block, SUM(bucket_count) AS buckets
		FROM `tabHarvest Log`
		WHERE docstatus < 2 AND date BETWEEN %(f)s AND %(t)s
		GROUP BY block""",
		{"f": from_date, "t": to_date},
		as_dict=True,
	)
	trees = {
		r.block: flt(r.effective_trees)
		for r in frappe.get_all("Coffee Block Profile", fields=["block", "effective_trees"],
								limit_page_length=0)
	}

	w_by_block = {r.block: r for r in weighed if r.block}
	l_by_block = {r.block: r for r in logged if r.block}

	total_kg = sum(flt(r.kg) for r in weighed)
	total_weighed_buckets = sum(flt(r.buckets) for r in weighed)
	estate_kg_per_bucket = (total_kg / total_weighed_buckets) if total_weighed_buckets else 0.0

	out = {}
	for blk in set(w_by_block) | set(l_by_block):
		w = w_by_block.get(blk)
		l = l_by_block.get(blk)
		weighed_kg = flt(w.kg) if w else 0.0
		weighed_buckets = flt(w.buckets) if w else 0.0
		logged_buckets = flt(l.buckets) if l else 0.0

		# Per-block rate where the block was weighed enough to have one, estate
		# rate otherwise. A block's own bucket fill differs from the estate's.
		rate = (weighed_kg / weighed_buckets) if weighed_buckets else estate_kg_per_bucket
		unweighed = max(logged_buckets - weighed_buckets, 0.0)
		kg = weighed_kg + unweighed * rate

		t = flt(trees.get(blk))
		out[blk] = {
			"block": blk,
			"kg": round(kg, 1),
			"weighed_kg": round(weighed_kg, 1),
			"buckets": round(max(logged_buckets, weighed_buckets), 1),
			"kg_per_bucket": round(rate, 2),
			"trees": t,
			"kg_per_tree": round(kg / t, 3) if t else 0.0,
			"from_date": str(from_date),
			"to_date": str(to_date),
		}

	setattr(frappe.local, CACHE_KEY, out)
	return out


def window_label(months=12):
	""""2025-06-01 to 2026-05-30" — shown next to every number derived from it."""
	any_row = next(iter(block_actuals(months).values()), None)
	return f"{any_row['from_date']} to {any_row['to_date']}" if any_row else ""
