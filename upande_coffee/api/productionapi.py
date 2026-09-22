# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Coffee production: the field side of the crop, as opposed to upande_coffee's
# post-harvest operations.
#
# The whole point of this module is that coffee is forecastable in a way roses
# are not. Rain triggers flowering, and ripe cherry follows roughly 30-35 weeks
# later — so a flowering logged in February sizes and dates the November crop.
# Everything here builds on that:
#
#   flowering events  -> WHEN the cherry arrives, week by week
#   berry samples     -> HOW MUCH is actually on the trees (counted, not guessed)
#   variety protocol  -> the baseline to compare both against, by tree age
#   picking rounds    -> whether it is being taken off in time
#
# Deliberately read-heavy: nearly every number below is derived from records the
# estate already captures (91k Harvest Logs, weighbridge tickets, outturns), so
# the module earns its keep before anyone types anything new.

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

# Single source of truth for what each flowering intensity is worth — shared with
# the doctype's own weight() so the form and the forecast can never disagree.
from upande_coffee.coffee_production.doctype.coffee_flowering_event.coffee_flowering_event import (
	INTENSITY_WEIGHT,
)
from upande_coffee.coffee_production.actuals import block_actuals


def _settings():
	return frappe.get_cached_doc("Coffee Production Settings")


def _week_start(d):
	"""Monday of the week containing d — the bucket everything is grouped into."""
	d = getdate(d)
	return add_days(d, -d.weekday())


def _active_season_name():
	return frappe.db.get_value("Coffee Season", {"is_active": 1}, "name")


@frappe.whitelist()
def resolve_season_target_kg(season=None):
	"""The season's target, in one place, so nothing has to choose between
	three numbers that mean different things:

	- Target  (this function): what management SET as the goal — top-down,
	  from a Coffee Budget doc, independent of the tree census.
	- Forecast (get_forecast / overview): what the tree-age curve and
	  flowering model EXPECT, bottom-up from the block census.
	- Override (Coffee Forecast Override): what a person on the ground
	  THINKS, block-week by block-week — sampled or walked.

	A Coffee Budget doc for the season, if one exists, wins — it is management
	saying "this is the number", block by block. Where no Coffee Budget has
	been created yet, Coffee Season.target_cherry_kg is kept as a season-total
	fallback, so sites that never adopt the new doctype are unaffected.
	"""
	season = season or _active_season_name()
	if not season:
		return 0.0

	if frappe.db.exists("Coffee Budget", season):
		total = frappe.db.sql(
			"SELECT SUM(target_kg) AS total FROM `tabCoffee Budget Block` WHERE parent = %s",
			season,
		)[0][0]
		if total:
			return flt(total)

	return flt(frappe.db.get_value("Coffee Season", season, "target_cherry_kg"))


def _season_actual_kg(season=None):
	"""Cherry actually received at the weighbridge so far this season — the
	third number alongside resolve_season_target_kg (what management set) and
	get_forecast (what the model expects), so a manager never has to guess
	which of the three a lone "kg" figure means."""
	season = season or _active_season_name()
	if not season:
		return 0.0
	dates = frappe.db.get_value("Coffee Season", season, ["start_date", "end_date"], as_dict=True)
	if not dates or not dates.start_date:
		return 0.0
	row = frappe.db.sql(
		"""SELECT SUM(total_weight_kg) FROM `tabHarvest Pickup`
		WHERE docstatus = 1 AND date BETWEEN %(f)s AND %(t)s""",
		{"f": dates.start_date, "t": dates.end_date or nowdate()},
	)
	return flt(row[0][0]) if row else 0.0


# ── Blocks ───────────────────────────────────────────────────────────────────


@frappe.whitelist()
def get_blocks():
	"""Every block profile with its census and baseline expectation."""
	rows = frappe.get_all(
		"Coffee Block Profile",
		fields=[
			"name", "block", "variety_protocol", "status", "area_ha", "year_planted",
			"tree_age", "tree_count", "gap_count", "effective_trees", "gap_pct",
			"expected_kg_cherry", "expected_kg_per_tree", "last_pruned", "pruning_cycle_years",
		],
		order_by="block",
		limit_page_length=0,
	)
	for r in rows:
		r["kg_per_ha"] = (flt(r.expected_kg_cherry) / flt(r.area_ha)) if flt(r.area_ha) else 0
	return rows


@frappe.whitelist()
def get_gap_report():
	"""Blocks ranked by yield lost to missing trees.

	Gaps are the quietest loss on a coffee estate: the block total just looks a
	bit low, year after year. Costing them makes the case for a gapping
	programme in one line.
	"""
	price = flt(_settings().cherry_price_per_kg)
	out = []
	for b in frappe.get_all(
		"Coffee Block Profile",
		filters={"status": "Active"},
		fields=["block", "variety_protocol", "tree_count", "gap_count", "gap_pct",
				"expected_kg_per_tree"],
		limit_page_length=0,
	):
		lost_kg = flt(b.gap_count) * flt(b.expected_kg_per_tree)
		if not lost_kg:
			continue
		out.append({
			"block": b.block,
			"variety": b.variety_protocol,
			"gaps": cint(b.gap_count),
			"gap_pct": flt(b.gap_pct),
			"lost_kg_cherry": lost_kg,
			"lost_value": lost_kg * price,
		})
	out.sort(key=lambda r: r["lost_kg_cherry"], reverse=True)
	return out


# ── Calibration ──────────────────────────────────────────────────────────────


@frappe.whitelist()
def get_yield_calibration(months=12):
	"""Expected vs actually delivered, block by block.

	The forecast is only worth what its per-tree yield is worth, and that number
	started life as a typed guess. This is the page that catches it: every block's
	expectation against what the weighbridge recorded, with the ratio, so a curve
	that is out by a factor of two is obvious instead of quietly halving a season.

	Read-only. Nothing here changes a number — saving a Variety Protocol does that,
	and it now pulls the same receipts.
	"""
	months = cint(months) or 12
	actuals = block_actuals(months=months)

	profiles = frappe.get_all(
		"Coffee Block Profile",
		fields=["block", "variety_protocol", "cycle_year", "status", "effective_trees",
				"expected_kg_per_tree", "expected_kg_cherry", "yield_basis"],
		limit_page_length=0,
	)

	rows, exp_total, act_total, trees_total = [], 0.0, 0.0, 0.0
	for p in profiles:
		a = actuals.get(p.block) or {}
		expected = flt(p.expected_kg_cherry)
		actual = flt(a.get("kg"))
		exp_total += expected
		act_total += actual
		trees_total += flt(p.effective_trees)
		rows.append({
			"block": p.block,
			"variety": p.variety_protocol,
			"cycle_year": p.cycle_year,
			"status": p.status,
			"trees": flt(p.effective_trees),
			"expected_kg_per_tree": flt(p.expected_kg_per_tree),
			"actual_kg_per_tree": flt(a.get("kg_per_tree")),
			"expected_kg": round(expected, 1),
			"actual_kg": round(actual, 1),
			# Above 1 means the block out-delivered its curve — the direction the
			# whole estate was out before receipts were used as the baseline.
			"ratio": round(actual / expected, 3) if expected else None,
			"basis": p.yield_basis,
			"kg_per_bucket": flt(a.get("kg_per_bucket")),
		})

	rows.sort(key=lambda r: -(r["actual_kg"] or 0))
	window = next(iter(actuals.values()), {})
	return {
		"rows": rows,
		"from_date": window.get("from_date"),
		"to_date": window.get("to_date"),
		"expected_kg": round(exp_total, 1),
		"actual_kg": round(act_total, 1),
		"ratio": round(act_total / exp_total, 3) if exp_total else None,
		"trees": trees_total,
		"expected_kg_per_tree": round(exp_total / trees_total, 3) if trees_total else 0,
		"actual_kg_per_tree": round(act_total / trees_total, 3) if trees_total else 0,
		"blocks_with_history": sum(1 for r in rows if r["actual_kg"]),
	}


# ── Forecast ─────────────────────────────────────────────────────────────────


def _triangular_spread(total, weeks):
	"""Split a total across `weeks` buckets, peaking in the middle.

	A flowering does not ripen on one day. A triangle is the honest shape: a
	build-up, a peak, a tail — and it beats a flat split for planning labour,
	because the peak is what you need pickers for.
	"""
	weeks = max(int(weeks), 1)
	if weeks == 1:
		return [total]
	mid = (weeks - 1) / 2.0
	# Weight falls off linearly from the middle, never to zero.
	weights = [max(mid + 1 - abs(i - mid), 0.5) for i in range(weeks)]
	denom = sum(weights)
	return [total * w / denom for w in weights]


@frappe.whitelist()
def get_forecast(from_date=None, to_date=None, block=None):
	"""Weekly cherry forecast per block, from flowering events.

	Each flowering event carries a weight (intensity x % of block). Its share of
	the block's expected crop is spread as a triangle over the ripening window
	the event itself computed. A berry sample, where one exists, overrides the
	variety-curve baseline — a count on the trees beats a curve every time.
	"""
	settings = _settings()
	spread = cint(settings.ripening_spread_weeks) or 6
	price = flt(settings.cherry_price_per_kg)

	profiles = {
		p.block: p
		for p in frappe.get_all(
			"Coffee Block Profile",
			filters={"block": block} if block else {},
			fields=["block", "variety_protocol", "status", "expected_kg_cherry", "effective_trees"],
			limit_page_length=0,
		)
	}
	if not profiles:
		return {"weeks": [], "blocks": [], "total_kg": 0, "total_value": 0}

	# Latest berry sample per block — the counted truth where we have it.
	sampled = {}
	for s in frappe.get_all(
		"Coffee Berry Sample",
		filters={"block": ["in", list(profiles)]},
		fields=["block", "sample_date", "est_block_kg"],
		order_by="sample_date asc",
		limit_page_length=0,
	):
		if flt(s.est_block_kg):
			sampled[s.block] = s  # later dates overwrite earlier ones

	events = frappe.get_all(
		"Coffee Flowering Event",
		filters={"block": ["in", list(profiles)]},
		fields=["name", "block", "flowering_date", "intensity", "pct_of_block",
				"expected_from_date", "expected_to_date", "expected_peak_date"],
		order_by="flowering_date",
		limit_page_length=0,
	)

	# block -> week_start -> kg
	grid = {}
	for ev in events:
		prof = profiles.get(ev.block)
		if not prof or prof.status in ("Stumped", "Abandoned"):
			continue

		base_kg = flt(sampled[ev.block].est_block_kg) if ev.block in sampled else flt(prof.expected_kg_cherry)
		if not base_kg:
			continue

		# Weight computed inline from the fields already fetched. Loading a
		# document per flowering event would be an N+1 in a page-facing loop, and
		# a season can carry dozens of events across the estate.
		weight = INTENSITY_WEIGHT.get(ev.intensity, 0.7) * (flt(ev.pct_of_block or 100) / 100.0)
		event_kg = base_kg * weight

		start = _week_start(ev.expected_from_date or ev.expected_peak_date or ev.flowering_date)
		for i, kg in enumerate(_triangular_spread(event_kg, spread)):
			wk = add_days(start, i * 7)
			grid.setdefault(ev.block, {}).setdefault(str(wk), 0)
			grid[ev.block][str(wk)] += kg

	# Bound the output to the window asked for, so a page can show a season.
	lo = getdate(from_date) if from_date else None
	hi = getdate(to_date) if to_date else None

	weeks = sorted({w for b in grid.values() for w in b})
	if lo:
		weeks = [w for w in weeks if getdate(w) >= _week_start(lo)]
	if hi:
		weeks = [w for w in weeks if getdate(w) <= hi]

	blocks = []
	for blk, byweek in sorted(grid.items()):
		series = [round(flt(byweek.get(w, 0)), 1) for w in weeks]
		total = sum(series)
		if not total:
			continue
		blocks.append({
			"block": blk,
			"variety": profiles[blk].variety_protocol,
			"basis": "berry count" if blk in sampled else "variety curve",
			"weeks": series,
			"total_kg": round(total, 1),
			"peak_week": weeks[series.index(max(series))] if series else None,
		})

	total_kg = sum(b["total_kg"] for b in blocks)
	return {
		"weeks": weeks,
		"blocks": blocks,
		"total_kg": round(total_kg, 1),
		"total_value": round(total_kg * price, 2),
		"weekly_total": [round(sum(b["weeks"][i] for b in blocks), 1) for i in range(len(weeks))],
	}


# ── Picking rounds ───────────────────────────────────────────────────────────


@frappe.whitelist()
def get_round_status():
	"""Which blocks are due or overdue a picking round.

	This is the alarm the module exists for. Cherry left past ripeness drops,
	ferments on the ground and pulls down the cup score of everything it is
	pulped with — so the cost of a late round is quality as well as quantity.

	"Days since" comes from Harvest Log, not from the round records, so a block
	nobody bothered to open a round for still shows up honestly.
	"""
	settings = _settings()
	interval = cint(settings.round_interval_days) or 14
	price = flt(settings.cherry_price_per_kg)
	today = getdate(nowdate())

	profiles = frappe.get_all(
		"Coffee Block Profile",
		filters={"status": "Active"},
		fields=["block", "variety_protocol", "expected_kg_cherry"],
		limit_page_length=0,
	)
	if not profiles:
		return []

	last_picked = {
		r.block: r.last_date
		for r in frappe.db.sql(
			"""SELECT block, MAX(date) AS last_date
			FROM `tabHarvest Log`
			WHERE docstatus < 2 AND block IN %(blocks)s
			GROUP BY block""",
			{"blocks": [p.block for p in profiles]},
			as_dict=True,
		)
	}

	rounds = {}
	for r in frappe.get_all(
		"Coffee Picking Round",
		fields=["block", "round_number", "status", "start_date"],
		order_by="start_date asc",
		limit_page_length=0,
	):
		rounds[r.block] = r

	out = []
	for p in profiles:
		last = last_picked.get(p.block)
		days = (today - getdate(last)).days if last else None
		# A block with a flowering ripening now is one we SHOULD be picking.
		in_window = frappe.db.exists(
			"Coffee Flowering Event",
			{"block": p.block, "expected_from_date": ["<=", today], "expected_to_date": [">=", today]},
		)
		overdue = bool(in_window) and (days is None or days > interval)
		out.append({
			"block": p.block,
			"variety": p.variety_protocol,
			"last_picked": str(last) if last else None,
			"days_since": days,
			"in_harvest_window": bool(in_window),
			"overdue": overdue,
			"current_round": cint(rounds[p.block].round_number) if p.block in rounds else 0,
			"round_status": rounds[p.block].status if p.block in rounds else None,
			# What is sitting out there unpicked, valued. Blunt on purpose.
			"at_risk_value": round(flt(p.expected_kg_cherry) * price, 2) if overdue else 0,
		})

	# Overdue first, then longest-waiting.
	out.sort(key=lambda r: (not r["overdue"], -(r["days_since"] or 0)))
	return out


# ── Workers ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def get_picker_performance(from_date=None, to_date=None, limit=40):
	"""Picker productivity, fairly compared.

	Two deliberate decisions here, both about not blaming people for their block:

	1. Effort is scored on BUCKETS per day, not kilograms. A picker controls how
	   many buckets they fill; how much cherry is in a bucket is a property of
	   the block. Ranking on kg/day just ranks the blocks people were sent to.
	   Kilograms are still reported, as value delivered.

	2. The comparison LEAVES THE PICKER OUT of their own benchmark. Scoring
	   against an average that includes yourself is degenerate — the only picker
	   on a block always lands on exactly 1.0, and a fast picker working beside a
	   slow one gets flattered by dragging the average down. Each picker is
	   measured against their peers on the same blocks, weighted by the days they
	   were actually there. With no peers the score is null, not 1.0: honest
	   about having nothing to compare with.
	"""
	to_date = to_date or nowdate()
	from_date = from_date or add_days(to_date, -30)

	rows = frappe.db.sql(
		"""SELECT harvester_id, block, COUNT(DISTINCT date) AS days,
			SUM(bucket_count) AS buckets
		FROM `tabHarvest Log`
		WHERE docstatus < 2 AND date BETWEEN %(f)s AND %(t)s
		GROUP BY harvester_id, block""",
		{"f": from_date, "t": to_date},
		as_dict=True,
	)
	if not rows:
		return {"pickers": [], "block_averages": [], "from_date": str(from_date), "to_date": str(to_date)}

	# Weighed kg per block over the window, and the implied kg per bucket. This
	# is measured, not a constant — it also tells you if bucket fill is slipping.
	weighed = {
		r.block: r
		for r in frappe.db.sql(
			"""SELECT hpd.block, SUM(hpd.weight_kg) AS kg, SUM(hpd.bucket_count) AS buckets
			FROM `tabHarvest Pickup Detail` hpd
			JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
			WHERE hp.docstatus = 1 AND hp.date BETWEEN %(f)s AND %(t)s
			GROUP BY hpd.block""",
			{"f": from_date, "t": to_date},
			as_dict=True,
		)
	}
	kg_per_bucket = {
		b: (flt(w.kg) / flt(w.buckets)) for b, w in weighed.items() if flt(w.buckets)
	}

	# Per-block totals in BUCKETS and picker-days, used to build each picker's
	# leave-one-out benchmark below.
	block_tot = {}
	for r in rows:
		t = block_tot.setdefault(r.block, {"buckets": 0.0, "picker_days": 0.0})
		t["buckets"] += flt(r.buckets)
		t["picker_days"] += flt(r.days)

	pickers = {}
	for r in rows:
		rate = kg_per_bucket.get(r.block)
		p = pickers.setdefault(r.harvester_id, {
			"harvester_id": r.harvester_id, "buckets": 0, "kg": 0.0,
			"days": 0, "blocks": set(), "rows": [],
		})
		p["buckets"] += cint(r.buckets)
		p["kg"] += flt(r.buckets) * rate if rate else 0
		p["days"] += cint(r.days)
		p["blocks"].add(r.block)
		p["rows"].append(r)

	names = {
		h.harvester_id: h.employee
		for h in frappe.get_all("Harvester", fields=["harvester_id", "employee"], limit_page_length=0)
	}

	out = []
	for p in pickers.values():
		# Peer benchmark: for each block this picker worked, what did EVERYONE
		# ELSE average per day there? Their own rows are subtracted out.
		peer_expected_buckets = 0.0
		peer_days = 0.0
		for r in p["rows"]:
			t = block_tot[r.block]
			others_buckets = t["buckets"] - flt(r.buckets)
			others_days = t["picker_days"] - flt(r.days)
			if others_days <= 0:
				continue  # sole picker on that block — nothing to compare against
			peer_expected_buckets += (others_buckets / others_days) * flt(r.days)
			peer_days += flt(r.days)

		out.append({
			"harvester_id": p["harvester_id"],
			"name": names.get(p["harvester_id"]) or p["harvester_id"],
			"buckets": p["buckets"],
			"kg": round(p["kg"], 1),
			"days": p["days"],
			"blocks": len(p["blocks"]),
			# Effort: what the picker actually controls.
			"buckets_per_day": round(p["buckets"] / p["days"], 2) if p["days"] else 0,
			# Value delivered: block-dependent, shown but not ranked on.
			"kg_per_day": round(p["kg"] / p["days"], 1) if p["days"] else 0,
			# 1.0 = matched their peers on the same blocks. None = no peers to
			# compare with, which is not the same as average.
			"vs_peers": round(p["buckets"] / peer_expected_buckets, 2) if peer_expected_buckets else None,
			"benchmarked_days": int(peer_days),
		})
	# Unbenchmarked pickers sort last rather than being treated as average.
	out.sort(key=lambda r: (r["vs_peers"] is None, -(r["vs_peers"] or 0)))

	return {
		"pickers": out[: cint(limit) or 40],
		"block_averages": [
			{
				"block": b,
				"buckets_per_picker_day": round(t["buckets"] / t["picker_days"], 2) if t["picker_days"] else 0,
				# How heavy a bucket is on this block — the block's contribution,
				# and a quiet check on whether fill is slipping.
				"kg_per_bucket": round(kg_per_bucket.get(b, 0), 1),
			}
			for b, t in sorted(block_tot.items())
		],
		"from_date": str(from_date),
		"to_date": str(to_date),
	}


# ── Block economics ──────────────────────────────────────────────────────────


@frappe.whitelist()
def get_block_economics(from_date=None, to_date=None):
	"""Cherry, cost and outturn per block — which blocks actually make money.

	Outturn ratio is measured from the estate's own Outturn Statements where the
	history allows, because a block converting cherry to clean coffee badly is
	telling you something (picking, pulping, or the block itself) that a
	textbook ratio would hide.
	"""
	to_date = to_date or nowdate()
	from_date = from_date or add_days(to_date, -365)
	settings = _settings()
	price = flt(settings.cherry_price_per_kg)

	weighed = frappe.db.sql(
		"""SELECT hpd.block, SUM(hpd.weight_kg) AS kg, SUM(hpd.bucket_count) AS buckets
		FROM `tabHarvest Pickup Detail` hpd
		JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
		WHERE hp.docstatus = 1 AND hp.date BETWEEN %(f)s AND %(t)s
		GROUP BY hpd.block""",
		{"f": from_date, "t": to_date},
		as_dict=True,
	)

	# Picking labour from the rate actually paid per bucket.
	labour = {
		r.block: flt(r.cost)
		for r in frappe.db.sql(
			"""SELECT hl.block, SUM(hl.bucket_count * IFNULL(cp.rate, 0)) AS cost
			FROM `tabHarvest Log` hl
			LEFT JOIN `tabCoffee Payment` cp
				ON cp.harvester_id = hl.harvester_id AND cp.docstatus = 1
			WHERE hl.docstatus < 2 AND hl.date BETWEEN %(f)s AND %(t)s
			GROUP BY hl.block""",
			{"f": from_date, "t": to_date},
			as_dict=True,
		)
	}

	profiles = {
		p.block: p
		for p in frappe.get_all(
			"Coffee Block Profile",
			fields=["block", "variety_protocol", "area_ha", "effective_trees", "expected_kg_cherry"],
			limit_page_length=0,
		)
	}

	out = []
	for w in weighed:
		kg = flt(w.kg)
		cost = flt(labour.get(w.block, 0))
		prof = profiles.get(w.block)
		expected = flt(prof.expected_kg_cherry) if prof else 0
		out.append({
			"block": w.block,
			"variety": prof.variety_protocol if prof else None,
			"area_ha": flt(prof.area_ha) if prof else 0,
			"cherry_kg": round(kg, 1),
			"expected_kg": round(expected, 1),
			"vs_expected_pct": round(kg / expected * 100, 1) if expected else None,
			"kg_per_ha": round(kg / flt(prof.area_ha), 1) if prof and flt(prof.area_ha) else None,
			"kg_per_tree": round(kg / flt(prof.effective_trees), 2) if prof and flt(prof.effective_trees) else None,
			"picking_cost": round(cost, 2),
			"cost_per_kg": round(cost / kg, 2) if kg else None,
			"gross_value": round(kg * price, 2),
			"margin_over_picking": round(kg * price - cost, 2),
		})
	out.sort(key=lambda r: r["cherry_kg"], reverse=True)
	return {"blocks": out, "from_date": str(from_date), "to_date": str(to_date)}


@frappe.whitelist()
def get_microlot_candidates():
	"""Blocks worth keeping separate through the mill.

	Specialty premiums are large and they are earned per lot, not per estate.
	A block that cups well and converts well is a candidate to pulp, dry and
	mill on its own rather than blending its identity away. Cup scores come from
	the Quality Inspections already being recorded.
	"""
	rows = frappe.db.sql(
		"""SELECT qi.batch_no, qi.item_code, qi.report_date,
			MAX(CASE WHEN r.specification = 'Cup Score' THEN r.reading_value END) AS cup_score,
			MAX(CASE WHEN r.specification = 'Moisture %%' THEN r.reading_value END) AS moisture
		FROM `tabQuality Inspection` qi
		JOIN `tabQuality Inspection Reading` r ON r.parent = qi.name
		WHERE qi.docstatus < 2
		GROUP BY qi.batch_no, qi.item_code, qi.report_date
		HAVING MAX(CASE WHEN r.specification = 'Cup Score' THEN r.reading_value END) IS NOT NULL
		ORDER BY CAST(MAX(CASE WHEN r.specification = 'Cup Score' THEN r.reading_value END) AS DECIMAL(10,2)) DESC
		LIMIT 40""",
		as_dict=True,
	)
	return rows


# ── One call for the page ────────────────────────────────────────────────────


@frappe.whitelist()
def overview(from_date=None, to_date=None):
	"""Everything the production page needs, in one round trip."""
	settings = _settings()
	forecast = get_forecast(from_date, to_date)
	rounds = get_round_status()
	gaps = get_gap_report()

	overdue = [r for r in rounds if r["overdue"]]
	return {
		"settings": {
			"ripening_weeks": cint(settings.ripening_weeks),
			"round_interval_days": cint(settings.round_interval_days),
			"cherry_price_per_kg": flt(settings.cherry_price_per_kg),
		},
		"headline": {
			"target_kg": resolve_season_target_kg(),
			"actual_kg": _season_actual_kg(),
			"forecast_kg": forecast["total_kg"],
			"forecast_value": forecast["total_value"],
			"blocks_forecast": len(forecast["blocks"]),
			"overdue_blocks": len(overdue),
			"at_risk_value": round(sum(r["at_risk_value"] for r in overdue), 2),
			"gap_loss_kg": round(sum(g["lost_kg_cherry"] for g in gaps), 1),
			"gap_loss_value": round(sum(g["lost_value"] for g in gaps), 2),
		},
		"forecast": forecast,
		"rounds": rounds,
		"gaps": gaps[:12],
		"blocks": get_blocks(),
	}
