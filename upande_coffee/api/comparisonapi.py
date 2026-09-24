# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Data for the Comparison tab on /coffee-dashboard: budget vs forecast vs
# actual, and planned vs paid labour cost, for the WHOLE active season —
# as opposed to the Planner tab's rolling 8-week grid window, or the
# Production tab's farm-level snapshot. Same three numbers everywhere
# (Coffee Budget Block, Coffee Production Forecast, weighbridge via
# productiongrid._actual_map) so this can never disagree with the Planner.

import frappe
from frappe.utils import flt, getdate

from upande_coffee.api.plannerapi import _season_targets
from upande_coffee.api.productionapi import _active_season_name, _settings
from upande_coffee.api.productiongrid import _actual_map, _iso, _span, _week_monday


def _season_pairs(start_date, end_date):
	"""Every (iso_year, iso_week) from a season's start to its end, inclusive."""
	start_date, end_date = getdate(start_date), getdate(end_date)
	sy, sw = _iso(start_date)
	count = ((end_date - start_date).days // 7) + 2  # generous; trimmed below
	pairs = _span(sy, sw, count)
	return [p for p in pairs if _week_monday(*p) <= end_date]


def _forecast_by_block_week(start_date, end_date):
	rows = frappe.get_all(
		"Coffee Production Forecast",
		filters={"week_start": ["between", [start_date, end_date]]},
		fields=["block", "week_start", "revised_forecast_kg"],
		limit_page_length=0,
	)
	return {(r.block, _iso(r.week_start)): flt(r.revised_forecast_kg) for r in rows}


def _payments_by_week(start_date, end_date):
	"""KES actually disbursed (submitted Coffee Payment), bucketed by iso week —
	same "docstatus == 1 is paid out" convention dashboardapi.get_payments uses."""
	rows = frappe.db.sql(
		"""SELECT date, SUM(total_payment) AS kes
		FROM `tabCoffee Payment`
		WHERE docstatus = 1 AND date BETWEEN %(f)s AND %(t)s
		GROUP BY date""",
		{"f": start_date, "t": end_date},
		as_dict=True,
	)
	out = {}
	for r in rows:
		key = _iso(r.date)
		out[key] = out.get(key, 0) + flt(r.kes)
	return out


@frappe.whitelist()
def get_comparison(season=None):
	season = season or _active_season_name()
	if not season or not frappe.db.exists("Coffee Season", season):
		return {"season": None}

	s = frappe.db.get_value("Coffee Season", season, ["start_date", "end_date"], as_dict=True)
	pairs = _season_pairs(s.start_date, s.end_date)
	week_dates = [
		{"year": y, "week": w, "start": str(_week_monday(y, w)),
		 "start_label": f"{_week_monday(y, w).day} {_week_monday(y, w):%b}"}
		for y, w in pairs
	]

	budgets = _season_targets(season)  # {block: target_kg}
	forecasts = _forecast_by_block_week(s.start_date, s.end_date)  # {(block, pair): kg}
	actuals = _actual_map(pairs)  # {(block, pair): kg}
	picker_rate = flt(_settings().picker_rate_per_kg)
	paid = _payments_by_week(s.start_date, s.end_date)  # {pair: kes}

	blocks_seen = set(budgets) | {b for b, _p in forecasts} | {b for b, _p in actuals}
	blocks_out = []
	for block in blocks_seen:
		fc = sum(kg for (b, _p), kg in forecasts.items() if b == block)
		ac = sum(kg for (b, _p), kg in actuals.items() if b == block)
		blocks_out.append({
			"block": block,
			"budget_kg": budgets.get(block) or 0,
			"forecast_kg": fc,
			"actual_kg": ac,
		})
	blocks_out.sort(key=lambda r: (r["actual_kg"] or r["forecast_kg"] or r["budget_kg"]), reverse=True)

	weeks_out = []
	for pr, wd in zip(pairs, week_dates):
		fc = sum(kg for (b, p), kg in forecasts.items() if p == pr)
		ac = sum(kg for (b, p), kg in actuals.items() if p == pr)
		planned_kes = fc * picker_rate
		paid_kes = paid.get(pr) or 0
		weeks_out.append({
			**wd,
			"forecast_kg": round(fc) or None,
			"actual_kg": round(ac) or None,
			"planned_kes": round(planned_kes) or None,
			"paid_kes": round(paid_kes) or None,
		})

	total_budget = sum(v for v in budgets.values())
	total_forecast = sum(r["forecast_kg"] for r in blocks_out)
	total_actual = sum(r["actual_kg"] for r in blocks_out)
	total_planned_kes = total_forecast * picker_rate
	total_paid_kes = sum(v for v in paid.values())

	return {
		"season": season,
		"start_date": str(s.start_date),
		"end_date": str(s.end_date),
		"summary": {
			"budget_kg": total_budget,
			"forecast_kg": total_forecast,
			"actual_kg": total_actual,
		},
		"blocks": blocks_out,
		"weeks": weeks_out,
		"cost": {
			"picker_rate_per_kg": picker_rate,
			"planned_kes": round(total_planned_kes),
			"paid_kes": round(total_paid_kes),
		},
	}
