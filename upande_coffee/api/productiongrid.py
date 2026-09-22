# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Payload for the /coffee-production grid.
#
# This deliberately mirrors upande_agriculture's `budget.grid_payload` key for
# key, because the page IS the budget-forecast page: same markup, same CSS, same
# grid machinery, sparklines and toolbar. Only the numbers differ. Keeping the
# contract identical is what lets the whole UI be reused rather than reimplemented
# — so if you change a key name here, the page stops drawing.
#
# The mapping from flowers to coffee:
#   greenhouse -> block            (the unit that is planted and picked)
#   variety    -> coffee variety   (SL28, Ruiru 11 …)
#   area (m²)  -> bearing trees    (what actually scales the yield)
#   stems      -> kg of cherry
#   budget     -> variety-curve baseline, from tree age
#   revised    -> flowering-based forecast (the number worth arguing about)
#   actual     -> weighbridge kilograms

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_coffee.api.productionapi import _settings, _triangular_spread
from upande_coffee.coffee_production.doctype.coffee_flowering_event.coffee_flowering_event import (
	INTENSITY_WEIGHT,
)

# The grid shows a rolling window rather than a whole year: a coffee season runs
# across a December, and nobody plans 52 weeks of picking at once.
WINDOW_WEEKS = 26
LOOKBACK_WEEKS = 6

MONTHS = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]


def _iso(d):
	c = getdate(d).isocalendar()
	return c[0], c[1]


def _week_monday(year, week):
	return datetime.date.fromisocalendar(year, week, 1)


def _weeks_in_iso_year(year):
	return datetime.date(year, 12, 28).isocalendar()[1]


def _span(start_year, start_week, count):
	"""`count` consecutive (iso_year, iso_week) pairs from a starting point."""
	pairs, y, w = [], int(start_year), int(start_week)
	for _i in range(count):
		if w < 1:
			y -= 1
			w = _weeks_in_iso_year(y)
		if w > _weeks_in_iso_year(y):
			w = 1
			y += 1
		pairs.append((y, w))
		w += 1
	return pairs


def _season_label(d):
	"""Kenyan coffee season straddles the new year; label it 2026/27."""
	d = getdate(d)
	return f"{d.year}/{str(d.year + 1)[-2:]}" if d.month >= 10 else f"{d.year - 1}/{str(d.year)[-2:]}"


def _forecast_series(profile, events, samples, pairs, spread):
	"""Flowering-based forecast for one block, aligned to `pairs`.

	Same model as productionapi.get_forecast — each flowering spread as a
	triangle over its ripening window — but bucketed onto this grid's exact
	week axis so the column headers and the numbers cannot drift apart.
	"""
	by_week = {}
	base_default = flt(profile.expected_kg_cherry)
	counted = samples.get(profile.block)
	base_kg = flt(counted.est_block_kg) if counted else base_default
	if not base_kg or profile.status in ("Stumped", "Abandoned"):
		return [None] * len(pairs), False

	for ev in events.get(profile.block, []):
		weight = INTENSITY_WEIGHT.get(ev.intensity, 0.7) * (flt(ev.pct_of_block or 100) / 100.0)
		start = getdate(ev.expected_from_date or ev.expected_peak_date or ev.flowering_date)
		start = add_days(start, -start.weekday())
		for i, kg in enumerate(_triangular_spread(base_kg * weight, spread)):
			key = _iso(add_days(start, i * 7))
			by_week[key] = by_week.get(key, 0) + kg

	series = [round(by_week.get(p), 1) if by_week.get(p) else None for p in pairs]
	return series, bool(counted)


def _baseline_series(profile, pairs, forecast):
	"""Variety-curve baseline, shaped like the forecast so the two compare.

	The curve says how much a block of this age SHOULD give in a season; it says
	nothing about which week. So it is spread across whatever weeks the flowering
	model put cherry in — that makes "budget vs forecast" a real comparison
	rather than a comparison against a flat line nobody believes.
	"""
	total = flt(profile.expected_kg_cherry)
	live = [i for i, v in enumerate(forecast) if v]
	if not total or not live:
		return [None] * len(pairs)
	share = total / len(live)
	return [round(share, 1) if i in set(live) else None for i in range(len(pairs))]


def _month_index(d):
	"""Oct-Sep season index, matching MONTHS above."""
	return (getdate(d).month - 10) % 12


def _monthly_actual(pairs):
	"""Weighbridge kilograms per (block, season month) for the season in view.

	Filling this is what makes the page's season curve and its actual-vs-expected
	tile work at all — they read the monthly series, and returning nulls left the
	whole dashboard blank of anything measured.
	"""
	if not pairs:
		return {}
	lo = _week_monday(*pairs[0])
	# A season curve wants the whole season, not just the grid window.
	lo = getdate(f"{lo.year - (1 if lo.month < 10 else 0)}-10-01")
	rows = frappe.db.sql(
		"""SELECT hpd.block, hp.date, SUM(hpd.weight_kg) AS kg
		FROM `tabHarvest Pickup Detail` hpd
		JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
		WHERE hp.docstatus = 1 AND hp.date >= %(f)s
		GROUP BY hpd.block, hp.date""",
		{"f": lo},
		as_dict=True,
	)
	out = {}
	for r in rows:
		key = (r.block, _month_index(r.date))
		out[key] = out.get(key, 0) + flt(r.kg)
	return out


def _monthly_expected(profile, events, samples, spread):
	"""Expected cherry per season month, from the same flowering model.

	Uses the flowering windows rather than a flat twelfth, so the curve peaks
	when the crop actually arrives — which is the entire point of the page.
	"""
	months = [0.0] * 12
	base = flt(samples[profile.block].est_block_kg) if profile.block in samples \
		else flt(profile.expected_kg_cherry)
	if not base or profile.status in ("Stumped", "Abandoned"):
		return [None] * 12
	for ev in events.get(profile.block, []):
		weight = INTENSITY_WEIGHT.get(ev.intensity, 0.7) * (flt(ev.pct_of_block or 100) / 100.0)
		start = getdate(ev.expected_from_date or ev.expected_peak_date or ev.flowering_date)
		for i, kg in enumerate(_triangular_spread(base * weight, spread)):
			months[_month_index(add_days(start, i * 7))] += kg
	return [round(m) if m else None for m in months]


def _loss_factor_map():
	"""Per block, the share of the crop already lost.

	Cherry on the ground, taken by berry borer or stripped by CBD is never going
	to reach the mill. Without subtracting it the forecast keeps promising a crop
	that no longer exists — the most damaging kind of wrong, because labour and
	mill capacity get booked against it.

	Losses within a season are additive but capped: a block cannot lose 130%.
	"""
	out = {}
	for r in frappe.get_all(
		"Coffee Crop Loss",
		fields=["block", "pct_of_crop"],
		limit_page_length=0,
	):
		out[r.block] = out.get(r.block, 0.0) + flt(r.pct_of_crop)
	return {b: min(pct, 95.0) / 100.0 for b, pct in out.items()}


def _override_map():
	"""(block, iso week) -> hand-set kilograms, with the reason kept alongside."""
	out = {}
	for r in frappe.get_all(
		"Coffee Forecast Override",
		fields=["block", "week_start", "kg_cherry", "reason"],
		limit_page_length=0,
	):
		out[(r.block, _iso(r.week_start))] = r
	return out


def _ripeness_map():
	"""Latest ripeness verdict per block — what the tree actually looks like."""
	out = {}
	for r in frappe.get_all(
		"Coffee Ripeness Observation",
		fields=["block", "obs_date", "readiness", "quality_risk", "pct_ripe",
				"pct_overripe", "pct_dropped"],
		order_by="obs_date asc",
		limit_page_length=0,
	):
		out[r.block] = r
	return out


def _actual_map(pairs):
	"""Weighbridge kilograms per (block, iso week) over the window."""
	if not pairs:
		return {}
	lo = _week_monday(*pairs[0])
	hi = add_days(_week_monday(*pairs[-1]), 6)
	rows = frappe.db.sql(
		"""SELECT hpd.block, hp.date, SUM(hpd.weight_kg) AS kg
		FROM `tabHarvest Pickup Detail` hpd
		JOIN `tabHarvest Pickup` hp ON hp.name = hpd.parent
		WHERE hp.docstatus = 1 AND hp.date BETWEEN %(f)s AND %(t)s
		GROUP BY hpd.block, hp.date""",
		{"f": lo, "t": hi},
		as_dict=True,
	)
	out = {}
	for r in rows:
		out[(r.block, _iso(r.date))] = out.get((r.block, _iso(r.date)), 0) + flt(r.kg)
	return out


def _coffee_dash(pairs, profiles, ripeness, losses, price):
	"""The numbers a coffee manager acts on, as opposed to admires.

	Ordered by how much money each one moves: cherry going over-ripe on the tree
	first, then trees that are missing entirely, then who is picking well, then
	which blocks are worth their inputs.
	"""
	from upande_coffee.api.productionapi import (
		get_block_economics,
		get_gap_report,
		get_picker_performance,
		get_round_status,
	)

	rounds = get_round_status()
	# A ripeness walk beats the calendar: if someone has looked at the tree, use
	# their verdict rather than a days-since rule.
	for r in rounds:
		obs = ripeness.get(r["block"])
		if obs:
			r["readiness"] = obs.get("readiness")
			r["quality_risk"] = obs.get("quality_risk")
			r["last_walked"] = str(obs.get("obs_date") or "")
			if obs.get("readiness") in ("Pick Now", "Overdue"):
				r["overdue"] = True
			elif obs.get("readiness") in ("Not Ready", "Approaching"):
				# Seen recently and not ready — not overdue whatever the calendar says.
				r["overdue"] = False
				r["at_risk_value"] = 0

	# Conversion: cherry in versus clean coffee out, per outturn. A block or
	# season converting badly is telling you something a textbook ratio hides.
	conversion = frappe.db.sql(
		"""SELECT os.name, os.outturn_number, os.parchment_weight, os.output_weight,
			os.milling_loss, os.grower
		FROM `tabOutturn Statement` os
		WHERE os.docstatus = 1 AND os.output_weight > 0
		ORDER BY os.creation DESC LIMIT 12""",
		as_dict=True,
	)
	for c in conversion:
		c["ratio"] = (round(flt(c.parchment_weight) / flt(c.output_weight), 2)
					  if flt(c.output_weight) else None)

	# Cup scores, so the best blocks can be kept separate and sold as micro-lots.
	cups = frappe.db.sql(
		"""SELECT qi.batch_no, qi.item_code, qi.report_date,
			MAX(CASE WHEN r.specification = 'Cup Score' THEN r.reading_value END) AS cup_score
		FROM `tabQuality Inspection` qi
		JOIN `tabQuality Inspection Reading` r ON r.parent = qi.name
		WHERE qi.docstatus < 2
		GROUP BY qi.batch_no, qi.item_code, qi.report_date
		HAVING MAX(CASE WHEN r.specification = 'Cup Score' THEN r.reading_value END) IS NOT NULL
		ORDER BY CAST(MAX(CASE WHEN r.specification = 'Cup Score' THEN r.reading_value END) AS DECIMAL(10,2)) DESC LIMIT 8""",
		as_dict=True,
	)

	# Rain already fallen that should produce a flowering soon — the earliest
	# possible warning that a crop is on its way.
	rain = frappe.get_all(
		"Coffee Rainfall Log",
		filters={"triggers_flowering": 1},
		fields=["reading_date", "block", "rainfall_mm", "expected_flowering_date"],
		order_by="reading_date desc",
		limit_page_length=8,
	)

	losses_detail = frappe.get_all(
		"Coffee Crop Loss",
		fields=["block", "loss_date", "cause", "severity", "pct_of_crop",
				"est_kg_lost", "est_value_lost"],
		order_by="est_value_lost desc",
		limit_page_length=10,
	)

	walks = frappe.get_all(
		"Coffee Ripeness Observation",
		fields=["block", "obs_date", "readiness", "quality_risk", "pct_ripe",
				"pct_overripe", "pct_dropped"],
		order_by="obs_date desc",
		limit_page_length=10,
	)

	pickers = get_picker_performance()
	econ = get_block_economics()
	gaps = get_gap_report()

	overdue = [r for r in rounds if r.get("overdue")]
	return {
		"rounds": rounds,
		"overdue_count": len(overdue),
		"at_risk_value": round(sum(flt(r.get("at_risk_value")) for r in overdue), 2),
		"gaps": gaps,
		"gap_loss_kg": round(sum(flt(g["lost_kg_cherry"]) for g in gaps), 1),
		"gap_loss_value": round(sum(flt(g["lost_value"]) for g in gaps), 2),
		"pickers": pickers.get("pickers", [])[:10],
		"picker_window": [pickers.get("from_date"), pickers.get("to_date")],
		"block_averages": pickers.get("block_averages", []),
		"economics": econ.get("blocks", [])[:12],
		"conversion": conversion,
		"cups": cups,
		"rain": rain,
		"losses": losses_detail,
		"loss_value_total": round(sum(flt(x.est_value_lost) for x in losses_detail), 2),
		"walks": walks,
		"blocks_never_walked": [
			p.block for p in profiles
			if p.status == "Active" and p.block not in ripeness
		],
	}


@frappe.whitelist()
def grid_payload(year=None, start_year=None, start_week=None, end_year=None, end_week=None):
	"""Everything the coffee production grid draws, in one round trip."""
	today = getdate(nowdate())
	iso_year, now_week = _iso(today)
	settings = _settings()
	spread = cint(settings.ripening_spread_weeks) or 6
	price = flt(settings.cherry_price_per_kg)

	if start_week and end_week:
		pairs = _span(start_year or iso_year, start_week, WINDOW_WEEKS)
		# Honour the requested end where it falls inside a sane span.
		want_end = (int(end_year or start_year or iso_year), int(end_week))
		if want_end in pairs:
			pairs = pairs[: pairs.index(want_end) + 1]
	else:
		first = _span(iso_year, now_week - LOOKBACK_WEEKS, 1)[0]
		pairs = _span(first[0], first[1], WINDOW_WEEKS)

	weeks = [w for _y, w in pairs]
	week_years = [y for y, _w in pairs]

	profiles = frappe.get_all(
		"Coffee Block Profile",
		fields=["name", "block", "variety_protocol", "status", "area_ha", "tree_age",
				"tree_count", "gap_count", "effective_trees", "gap_pct",
				"expected_kg_cherry", "expected_kg_per_tree"],
		order_by="block",
		limit_page_length=0,
	)

	events = {}
	for ev in frappe.get_all(
		"Coffee Flowering Event",
		fields=["block", "flowering_date", "intensity", "pct_of_block",
				"expected_from_date", "expected_peak_date"],
		order_by="flowering_date",
		limit_page_length=0,
	):
		events.setdefault(ev.block, []).append(ev)

	samples = {}
	for s in frappe.get_all(
		"Coffee Berry Sample",
		fields=["block", "sample_date", "est_block_kg"],
		order_by="sample_date asc",
		limit_page_length=0,
	):
		if flt(s.est_block_kg):
			samples[s.block] = s

	actuals = _actual_map(pairs)
	m_actuals = _monthly_actual(pairs)
	losses = _loss_factor_map()
	overrides = _override_map()
	ripeness = _ripeness_map()

	blocks, budget_total, forecast_total = [], 0.0, 0.0
	for p in profiles:
		forecast, counted = _forecast_series(p, events, samples, pairs, spread)
		baseline = _baseline_series(p, pairs, forecast)
		actual = [round(actuals.get((p.block, pr)), 1) or None if actuals.get((p.block, pr)) else None
				  for pr in pairs]

		# Losses scale the whole curve down; an override replaces a single week
		# outright, because a human who has walked the block beats the model.
		loss = losses.get(p.block, 0.0)
		if loss:
			forecast = [round(v * (1 - loss), 1) if v else v for v in forecast]
		for i, pr in enumerate(pairs):
			ov = overrides.get((p.block, pr))
			if ov:
				forecast[i] = flt(ov.kg_cherry)

		m_expected = _monthly_expected(p, events, samples, spread)
		if loss:
			m_expected = [round(v * (1 - loss)) if v else v for v in m_expected]
		annual = flt(p.expected_kg_cherry) * (1 - loss)
		budget_total += annual
		forecast_total += sum(v for v in forecast if v)

		blocks.append({
			"key": p.name,
			# The page's own vocabulary — kept so its markup needs no edits.
			"greenhouse": p.block,
			"variety": p.variety_protocol or "—",
			"area": cint(p.effective_trees),
			"rate": round(flt(p.expected_kg_per_tree), 2) or None,
			"budget_total": round(annual),
			"revision": 1,
			"weekly": {
				"grades": [{"grade": "all", "values": forecast, "revised": [None] * len(pairs)}],
				"revised": forecast,
				"budget": baseline,
				"actual": actual,
			},
			"monthly": {
				"grades": [{"grade": "all", "values": m_expected, "revised": [None] * 12}],
				"revised": m_expected,
				"budget": m_expected,
				"actual": [round(m_actuals.get((p.block, i)), 1) if m_actuals.get((p.block, i)) else None
						   for i in range(12)],
			},
			"lifetime": {"years": [], "budget": [], "actual": []},
			# Coffee-specific extras the page shows in its detail panels.
			"coffee": {
				"status": p.status,
				"tree_age": cint(p.tree_age),
				"trees": cint(p.tree_count),
				"gaps": cint(p.gap_count),
				"gap_pct": flt(p.gap_pct),
				"basis": "berry count" if counted else "variety curve",
				"value": round(sum(v for v in forecast if v) * price, 2),
				"loss_pct": round(loss * 100, 1),
				"overrides": len([1 for pr in pairs if (p.block, pr) in overrides]),
				"readiness": (ripeness.get(p.block) or {}).get("readiness"),
				"quality_risk": (ripeness.get(p.block) or {}).get("quality_risk"),
				"last_walked": str((ripeness.get(p.block) or {}).get("obs_date") or "") or None,
			},
		})

	return {
		"crop_year": _season_label(today),
		"year": iso_year,
		"weeks": weeks,
		"week_years": week_years,
		"week_dates": [
			{
				"start": str(_week_monday(y, w)),
				"end": str(add_days(_week_monday(y, w), 6)),
				"start_label": f"{_week_monday(y, w).day} {_week_monday(y, w):%b}",
				"end_label": f"{add_days(_week_monday(y, w), 6).day} {add_days(_week_monday(y, w), 6):%b}",
				"span": f"{_week_monday(y, w):%d %b} – {add_days(_week_monday(y, w), 6):%d %b}",
			}
			for y, w in pairs
		],
		"week_rule": "iso",
		"week_rule_label": "ISO weeks (Mon–Sun)",
		"weeks_in_year": _weeks_in_iso_year(iso_year),
		"iso_year": iso_year,
		"months": MONTHS,
		"now_week": now_week,
		"now_month": MONTHS[(today.month - 10) % 12],
		"house_count": len({b["greenhouse"] for b in blocks}),
		"blocks": blocks,
		"budget_total": round(budget_total),
		"forecast_total": round(forecast_total),
		"actual_vs_budget": None,
		"actual_weeks": len([1 for y, w in pairs if (y, w) < (iso_year, now_week)]),
		"model_error": None,
		"model_note": "forecast follows flowering; log flowering after each rain",
		"revision": 1,
		"revision_note": None,
		"site": frappe.db.get_single_value("Global Defaults", "default_company") or "Estate",
		"climate": {},
		"climate_note": "",
		"climate_alert": None,
		"light_norm": 0,
		# Coffee-specific analytics for the dashboard cards. Everything a coffee
		# manager actually acts on, computed here so the page needs one call.
		"coffee_dash": _coffee_dash(pairs, profiles, ripeness, losses, price),
		# Units, so the page can label itself without knowing it is coffee.
		"unit": "kg",
		"unit_long": "kg cherry",
		"cherry_price_per_kg": price,
	}


# ── Cell editing ─────────────────────────────────────────────────────────────
# These now genuinely save, because Coffee Forecast Override exists to hold a
# hand-set week with its reason and an audit trail. The earlier version threw —
# which was right at the time, since silently discarding a planner's number is
# the worst possible behaviour.


@frappe.whitelist()
def set_forecast_cell(block=None, greenhouse=None, week=None, year=None, value=None,
                      reason=None, **kwargs):
	"""Override one block-week of the forecast.

	`greenhouse` is accepted as an alias because the page speaks the flower
	vocabulary throughout — its grid sends whatever it called the row.
	"""
	block = block or greenhouse
	if not block or not week:
		frappe.throw(_("A block and a week are required."))

	iso_year = cint(year) or _iso(getdate(nowdate()))[0]
	monday = _week_monday(iso_year, cint(week))

	existing = frappe.db.exists("Coffee Forecast Override", {"block": block, "week_start": monday})
	if value in (None, "", "None"):
		# Clearing a cell removes the override and hands the week back to the model.
		if existing:
			frappe.delete_doc("Coffee Forecast Override", existing, ignore_permissions=True)
		return {"cleared": True, "block": block, "week": cint(week)}

	doc = (frappe.get_doc("Coffee Forecast Override", existing) if existing
		   else frappe.new_doc("Coffee Forecast Override"))
	doc.block = block
	doc.week_start = monday
	doc.kg_cherry = flt(value)
	doc.reason = reason or doc.reason or "Set from the production grid"
	doc.save(ignore_permissions=True)
	return {"saved": doc.name, "block": block, "week": cint(week), "kg": flt(value)}


@frappe.whitelist()
def set_budget_cell(**kwargs):
	"""The baseline comes from tree age and the variety curve, by design.

	Editing it here would mean the number on screen no longer follows from the
	census, and nobody could say why a block expects what it expects.
	"""
	frappe.throw(
		_(
			"The expected column is computed from tree age and the variety's yield "
			"curve. Change the Block Profile or the Variety Protocol instead — then "
			"every block using that variety moves together, which is the point. To "
			"overrule a single week, edit the forecast row."
		)
	)


@frappe.whitelist()
def revise_blocks(**kwargs):
	"""Revisions are per-week overrides here, so there is nothing to bump."""
	return {"revision": 1}


@frappe.whitelist()
def generate_all_budgets(**kwargs):
	"""The coffee equivalent of "rebuild budgets": re-apply the yield curve to
	every block profile, which is what saving one does."""
	names = frappe.get_all("Coffee Block Profile", pluck="name")
	for n in names:
		frappe.get_doc("Coffee Block Profile", n).save(ignore_permissions=True)
	return {"rebuilt": len(names)}


@frappe.whitelist()
def cell_history(block=None, greenhouse=None, week=None, year=None, **kwargs):
	"""Who overruled this week, to what, and why."""
	block = block or greenhouse
	if not (block and week):
		return []
	monday = _week_monday(cint(year) or _iso(getdate(nowdate()))[0], cint(week))
	name = frappe.db.exists("Coffee Forecast Override", {"block": block, "week_start": monday})
	if not name:
		return []
	doc = frappe.get_doc("Coffee Forecast Override", name)
	return [{
		"value": flt(doc.kg_cherry),
		"reason": doc.reason,
		"owner": doc.owner,
		"modified": str(doc.modified),
		"model_kg": flt(doc.model_kg),
	}]
