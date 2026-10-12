# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Payload for the /coffee-dashboard "Planner" tab: a weekly, per-block
# labour plan, turned into a headcount via a simple kg-per-worker rate.
#
# Storage is Coffee Harvest Plan / Coffee Harvest Plan Block -- the same
# doctype "Save week" writes (see save_week_plan below) and the one that
# already raises a real Expense Claim on submit. There is no longer a
# separate, coffee-only forecast doctype in the loop: Coffee Production
# Forecast and the short-lived Production Plan Task detour are both dead,
# left in place unused rather than deleted.
#
# One real regression from the Production Forecast era: kg_per_worker /
# workers_needed overrides had a home there (per block/week) and don't on
# Harvest Plan Block, which is a costing table, not a labour-planning one.
# computed_workers_needed still works (forecast kg / the farm default rate);
# a hand-set override just doesn't persist. Revisit if that's missed.
#
# This is deliberately NOT the flowering-model forecast (productiongrid.py)
# or the Coffee Forecast Override a picker sets from Kahawa Trail
# (pickupapi.weekly_forecast_vs_actual) — those answer "what does the model
# say", this answers "what did management decide, and how many people does
# that need". All three read the same weighbridge actuals so they can never
# disagree about what actually happened.

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_coffee.api.productiongrid import _actual_map, _iso, _span, _week_monday
from upande_coffee.api.productionapi import _active_season_name, _settings, resolve_season_target_kg

WINDOW_WEEKS = 8


def _kg_per_debe():
	return flt(_settings().kg_per_debe) or 15.5


def coffee_tasks(pairs, blocks=None):
	"""{(block, (year, week)): row} sourced from Coffee Harvest Plan Block --
	the plan whose week (from_date) is in the window, converted back to kg via
	kg_per_debe. A Draft plan counts the same as an Approved/submitted one;
	only a cancelled plan (docstatus 2) is excluded."""
	if not pairs:
		return {}
	wanted_weeks = set(pairs)
	lo, hi = _week_monday(*pairs[0]), _week_monday(*pairs[-1])
	plans = frappe.get_all(
		"Coffee Harvest Plan",
		filters={"from_date": ["between", [lo, hi]], "docstatus": ["<", 2]},
		fields=["name", "from_date"],
	)
	kg_per_debe = _kg_per_debe()
	out = {}
	for p in plans:
		pr = _iso(p.from_date)
		if pr not in wanted_weeks:
			continue
		rows = frappe.get_all(
			"Coffee Harvest Plan Block", filters={"parent": p.name}, fields=["block", "expected_debes"],
		)
		for r in rows:
			if blocks and r.block not in blocks:
				continue
			out[(r.block, pr)] = frappe._dict(
				target=flt(r.expected_debes) * kg_per_debe, kg_per_worker=None, workers_needed=None,
			)
	return out


def _week_plan(year, week):
	"""Get-or-create the Draft Coffee Harvest Plan for this week."""
	monday = _week_monday(year, week)
	name = frappe.db.get_value("Coffee Harvest Plan", {"from_date": monday, "docstatus": 0}, "name")
	if name:
		return frappe.get_doc("Coffee Harvest Plan", name)
	doc = frappe.new_doc("Coffee Harvest Plan")
	doc.from_date = monday
	doc.to_date = add_days(monday, 6)
	doc.season = _active_season_name()
	return doc


def upsert_coffee_task(block, year, week, kg, kg_per_worker=None, workers_needed=None):
	"""One block's row on that week's Draft Coffee Harvest Plan -- the quick-
	edit cell's save path. kg_per_worker/workers_needed are accepted only for
	call-site compatibility with the old signature; Harvest Plan Block has no
	field for either (see the module docstring)."""
	doc = _week_plan(year, week)
	if not doc.company:
		doc.company = frappe.db.get_value("Warehouse", block, "company")
	kg_per_debe = _kg_per_debe()
	wage_rate = flt(_settings().picker_rate_per_kg)
	row = next((r for r in doc.blocks if r.block == block), None) or doc.append("blocks", {"block": block})
	row.expected_debes = round(flt(kg) / kg_per_debe, 1) if kg_per_debe else 0
	row.rate_per_debe = round(wage_rate * kg_per_debe, 2)
	doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)
	return doc.name


def _blocks():
	return frappe.get_all(
		"Coffee Block Profile",
		filters={"status": "Active"},
		fields=["block", "variety_protocol"],
		order_by="block",
		limit_page_length=0,
	)


def _default_rate():
	return flt(_settings().default_kg_per_worker)


def _effective_rate(kg_per_worker):
	return flt(kg_per_worker) or _default_rate()


def _season_targets(season):
	"""{block: season target kg} from Coffee Budget — the block-level half of
	the same three-way comparison productionapi.resolve_season_target_kg
	already gives at farm level."""
	if not season or not frappe.db.exists("Coffee Budget", season):
		return {}
	return {
		r.block: flt(r.target_kg)
		for r in frappe.get_all(
			"Coffee Budget Block", filters={"parent": season}, fields=["block", "target_kg"]
		)
	}


@frappe.whitelist()
def get_planner_grid(start_year=None, start_week=None, weeks=WINDOW_WEEKS, season=None):
	"""Every block x week in the window: revised forecast, actual, and the
	labour it implies, plus farm totals and the season budget alongside."""
	today = getdate(nowdate())
	iso_year, now_week = _iso(today)
	# One week of lookback by default, same as pickupapi.weekly_forecast_vs_actual,
	# so a row entered for "this week" late doesn't immediately scroll off the grid.
	if start_year or start_week:
		first = (cint(start_year) or iso_year, cint(start_week))
	else:
		first = _span(iso_year, now_week - 1, 1)[0]
	pairs = _span(first[0], first[1], cint(weeks) or WINDOW_WEEKS)

	season = season or _active_season_name()
	targets = _season_targets(season)

	# Weighbridge kg is always read fresh here (same helper the /coffee-production
	# grid uses) rather than trusted from whatever a row's actual_kg last saved
	# as — a week that has since been weighed must not show stale zeros.
	actuals = _actual_map(pairs)
	records = coffee_tasks(pairs)

	week_totals = [{"forecast_kg": 0.0, "actual_kg": 0.0, "workers_needed": 0.0} for _p in pairs]

	blocks_out = []
	for b in _blocks():
		cells = []
		for i, pr in enumerate(pairs):
			rec = records.get((b.block, pr))
			revised = flt(rec.target) if rec else 0.0
			kg_per_worker = flt(rec.kg_per_worker) if rec else 0.0
			rate = _effective_rate(kg_per_worker)
			computed = round(revised / rate, 1) if revised and rate else 0.0
			override = flt(rec.workers_needed) if rec else 0.0
			workers = override or computed
			actual = flt(actuals.get((b.block, pr)))

			cells.append({
				"year": pr[0], "week": pr[1],
				"revised_forecast_kg": revised or None,
				"actual_kg": actual or None,
				"kg_per_worker": kg_per_worker or None,
				"computed_workers_needed": computed or None,
				"workers_needed": workers or None,
				"is_override": bool(override),
			})
			week_totals[i]["forecast_kg"] += revised
			week_totals[i]["actual_kg"] += actual
			week_totals[i]["workers_needed"] += workers

		blocks_out.append({
			"block": b.block,
			"variety": b.variety_protocol or "—",
			"season_target_kg": targets.get(b.block),
			"cells": cells,
		})

	week_dates = [
		{
			"year": y, "week": w,
			"start": str(_week_monday(y, w)),
			"start_label": f"{_week_monday(y, w).day} {_week_monday(y, w):%b}",
		}
		for y, w in pairs
	]

	peak = max(week_totals, key=lambda t: t["workers_needed"], default=None)
	peak_idx = week_totals.index(peak) if peak else None
	this_idx = next((i for i, (y, w) in enumerate(pairs) if (y, w) == (iso_year, now_week)), None)

	return {
		"season": season,
		"weeks": week_dates,
		"this_week_index": this_idx,
		"blocks": blocks_out,
		"week_totals": [
			{
				"year": pr[0], "week": pr[1],
				"forecast_kg": round(t["forecast_kg"]) or None,
				"actual_kg": round(t["actual_kg"]) or None,
				"workers_needed": round(t["workers_needed"], 1) or None,
			}
			for pr, t in zip(pairs, week_totals)
		],
		"summary": {
			"season_target_kg": resolve_season_target_kg(season),
			# Season budget card needs to say plainly when there's no Coffee Budget doc
			# and resolve_season_target_kg has fallen back to Coffee Season.target_cherry_kg.
			"budget_set": bool(season and frappe.db.exists("Coffee Budget", season)),
			"window_forecast_kg": round(sum(t["forecast_kg"] for t in week_totals)) or None,
			"window_actual_kg": round(sum(t["actual_kg"] for t in week_totals)) or None,
			"this_week_workers_needed": round(week_totals[this_idx]["workers_needed"], 1)
				if this_idx is not None else None,
			"peak_week_workers_needed": round(peak["workers_needed"], 1) if peak else None,
			"peak_week": week_dates[peak_idx]["start_label"] if peak_idx is not None else None,
		},
		"default_kg_per_worker": _default_rate(),
		# The wage rate, not the productivity rate above — what a manager actually
		# requests in cash to pay pickers for a block's forecast (kg x this rate).
		"picker_rate_per_kg": flt(_settings().picker_rate_per_kg),
	}


@frappe.whitelist(methods=["POST"])
def save_forecast_cell(block, week_start, revised_forecast_kg=None, kg_per_worker=None, workers_needed=None):
	"""Upsert one block/week row of the planner grid. Any of the three value
	fields left out of the call keeps whatever the row already had."""
	if not block or not week_start:
		frappe.throw(_("A block and a week are required."))

	year, week = _iso(getdate(week_start))
	current = coffee_tasks([(year, week)], [block]).get((block, (year, week)))

	kg = flt(revised_forecast_kg) if revised_forecast_kg not in (None, "") else flt(current.target if current else 0)
	rate = flt(kg_per_worker) if kg_per_worker not in (None, "") else (flt(current.kg_per_worker) if current else None)
	workers = flt(workers_needed) if workers_needed not in (None, "") else (flt(current.workers_needed) if current else None)

	upsert_coffee_task(block, year, week, kg, rate, workers)

	effective_rate = _effective_rate(rate)
	computed = round(kg / effective_rate, 1) if kg and effective_rate else 0.0

	return {
		"saved": block,
		"block": block,
		"week_start": str(_week_monday(year, week)),
		"revised_forecast_kg": kg or None,
		"actual_kg": flt(_actual_map([(year, week)]).get((block, (year, week)))) or None,
		"kg_per_worker": rate or None,
		"computed_workers_needed": computed or None,
		"workers_needed": workers or computed or None,
	}


@frappe.whitelist(methods=["POST"])
def save_week_plan(week_start):
	"""Get-or-create a Draft Coffee Harvest Plan for the given week, one block
	row per block that currently carries a forecast -- the "Save" action at the
	bottom of the Planner grid's week column. Stays a Draft: submitting (which
	raises a real Expense Claim) is a separate, deliberate step on the Harvest
	Plan itself, not something a grid save should trigger."""
	year, week = _iso(getdate(week_start))
	monday = _week_monday(year, week)
	records = coffee_tasks([(year, week)])
	wanted = [(b.block, records.get((b.block, (year, week)))) for b in _blocks()]
	wanted = [(block, rec) for block, rec in wanted if rec and rec.target]

	if not wanted:
		return {"created": None, "message": _("No block has a forecast this week to save.")}

	settings = _settings()
	kg_per_debe = flt(settings.kg_per_debe) or 15.5
	wage_rate = flt(settings.picker_rate_per_kg)
	rate_per_debe = round(wage_rate * kg_per_debe, 2)

	name = frappe.db.get_value("Coffee Harvest Plan", {"from_date": monday, "docstatus": 0}, "name")
	doc = frappe.get_doc("Coffee Harvest Plan", name) if name else frappe.new_doc("Coffee Harvest Plan")
	if doc.is_new():
		doc.from_date = monday
		doc.to_date = add_days(monday, 6)
		doc.season = _active_season_name()
		doc.company = frappe.db.get_value("Warehouse", wanted[0][0], "company")

	rows_by_block = {row.block: row for row in doc.blocks}
	for block, rec in wanted:
		row = rows_by_block.get(block) or doc.append("blocks", {"block": block})
		row.expected_debes = round(flt(rec.target) / kg_per_debe, 1)
		row.rate_per_debe = rate_per_debe
		rows_by_block[block] = row

	doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)
	return {
		"created": doc.name,
		"harvest_plan": doc.name,
		"workflow_state": doc.workflow_state,
		"total_debes": doc.total_debes,
		"total_cost": doc.total_cost,
	}


