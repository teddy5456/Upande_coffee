# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Backdated Coffee Payments + duplicate-payment audit.
#   Backdated Payments / By Pay Day: a payment is backdated when it pays more buckets than
#     the harvester logged on the pay date (picker collected late).
#   Duplicate Payments / Harvester Summary: replays every payment in order; buckets paid
#     beyond everything logged by then were paid twice. Cause: before 2026-10-14 a backdated
#     payment marked only its own day's logs paid, so the older logs were offered again.
#
# The same body runs on live as a non-standard report script (Server Script sandbox), which
# is why it avoids tuple unpacking and augmented assignment on subscripts.

import json

import frappe


def execute(filters=None):
	f = filters or {}
	fd = f.get("from_date") or frappe.utils.add_days(frappe.utils.nowdate(), -30)
	td = f.get("to_date") or frappe.utils.nowdate()
	view = f.get("view") or "Backdated Payments"
	params = {"f": fd, "t": td}
	extra = ""
	if f.get("harvester"):
		extra = " and cp.harvester_id = %(h)s"
		params["h"] = f.get("harvester")

	if view in ["Duplicate Payments", "Harvester Summary"]:
		# Replay every payment in the order it was made. At each one: what had been logged
		# by then, what had been paid by then, and what the app offered as unpaid. Buckets
		# paid beyond everything logged by then were paid twice. The usual cause: an earlier
		# payment covered backdated buckets but (before 2026-10-14) marked only its own day's
		# logs paid, so the older logs stayed "unpaid" and were offered again.
		season = frappe.db.get_value("Coffee Season", {"is_active": 1}, "start_date") or "2026-07-02"
		params["s"] = season
		pays = frappe.db.sql("""select cp.name, cp.harvester_id, cp.date, cp.total_buckets, cp.rate,
				cp.harvest_log_names, cp.creation
			from `tabCoffee Payment` cp where cp.docstatus = 1""" + extra + """
			order by cp.harvester_id, cp.creation""", params, as_dict=True)
		logs = frappe.db.sql("""select l.name, l.harvester_id, l.date, l.bucket_count, l.creation, l.paid
			from `tabHarvest Log` l
			where l.date >= %(s)s and l.harvester_id in (select distinct cp.harvester_id from `tabCoffee Payment` cp
				where cp.docstatus = 1""" + extra + """)
			order by l.creation""", params, as_dict=True)
		logs_by = {}
		for l in logs:
			if l.harvester_id not in logs_by:
				logs_by[l.harvester_id] = []
			logs_by[l.harvester_id].append(l)
		pays_by = {}
		order = []
		for p in pays:
			if p.harvester_id not in pays_by:
				pays_by[p.harvester_id] = []
				order.append(p.harvester_id)
			pays_by[p.harvester_id].append(p)

		dup_rows = []
		per_h = []
		tot_dup = 0
		tot_dup_amt = 0
		tot_risk = 0
		tot_risk_amt = 0
		for h in order:
			ls = logs_by.get(h) or []
			marked = {}
			causes = []
			cum_paid = 0
			h_dup = 0
			h_dup_amt = 0
			first_dup = None
			last_rate = 0
			for p in pays_by[h]:
				last_rate = frappe.utils.flt(p.rate)
				existing = [l for l in ls if l.creation <= p.creation]
				logged_before = 0
				offered = 0
				own_day = 0
				for l in existing:
					logged_before = logged_before + (l.bucket_count or 0)
					if l.name not in marked:
						offered = offered + (l.bucket_count or 0)
						if l.date == p.date:
							own_day = own_day + (l.bucket_count or 0)
				paid_before = cum_paid
				cum_paid = cum_paid + (p.total_buckets or 0)
				# paid beyond everything logged so far, net of duplicates already counted
				excess = cum_paid - logged_before
				dup = excess - h_dup if excess > h_dup else 0
				if dup > 0:
					need = dup
					src = []
					for c in causes:
						if need <= 0:
							break
						if c["left"] <= 0:
							continue
						take = c["left"] if c["left"] < need else need
						c["left"] = c["left"] - take
						need = need - take
						src.append(c["payment"] + " (" + frappe.utils.formatdate(c["date"], "dd MMM") + ", " + str(take) + ")")
					if src:
						why = ("Paid again for buckets already paid on " + ", ".join(src) + ". That payment included "
							+ "earlier days but marked only its own day's logs paid, so the app offered them again.")
					else:
						why = "Paid for buckets that had already been paid - the app still showed them as unpaid."
					amt = dup * frappe.utils.flt(p.rate)
					h_dup = h_dup + dup
					h_dup_amt = h_dup_amt + amt
					if not first_dup:
						first_dup = p.date
					if str(p.date) >= str(fd) and str(p.date) <= str(td):
						dup_rows.append({"date": p.date, "payment": p.name, "harvester": h,
							"total_buckets": p.total_buckets, "offered": offered, "logged": logged_before,
							"paid_before": paid_before, "dup": dup, "dup_amt": amt,
							"source": ", ".join(src) if src else "", "why": why})
				# mark logs the way the system did at the time
				names = json.loads(p.harvest_log_names) if p.harvest_log_names else []
				if names:
					for n in names:
						marked[n] = 1
				else:
					for l in existing:
						if l.date == p.date:
							marked[l.name] = 1
					left = (p.total_buckets or 0) - own_day
					if left > 0:
						causes.append({"payment": p.name, "date": p.date, "left": left})
			# still flagged unpaid although already paid for: offered again at the next payout
			flagged_paid = 0
			flagged_unpaid = 0
			logged = 0
			for l in ls:
				logged = logged + (l.bucket_count or 0)
				if l.paid:
					flagged_paid = flagged_paid + (l.bucket_count or 0)
				else:
					flagged_unpaid = flagged_unpaid + (l.bucket_count or 0)
			gap = cum_paid - flagged_paid
			risk = 0
			if gap > 0:
				risk = gap if gap < flagged_unpaid else flagged_unpaid
			tot_dup = tot_dup + h_dup
			tot_dup_amt = tot_dup_amt + h_dup_amt
			tot_risk = tot_risk + risk
			tot_risk_amt = tot_risk_amt + risk * last_rate
			if h_dup > 0 or risk > 0:
				beyond = cum_paid - logged if cum_paid > logged else 0
				per_h.append({"harvester": h, "logged": logged, "paid": cum_paid, "payments": len(pays_by[h]),
					"dup": h_dup, "dup_amt": h_dup_amt, "first_dup": first_dup, "beyond": beyond,
					"beyond_amt": beyond * last_rate, "risk": risk, "risk_amt": risk * last_rate})

		summary = [
			{"label": "Paid twice (season)", "value": tot_dup_amt, "datatype": "Currency", "indicator": "Red"},
			{"label": "Buckets paid twice", "value": tot_dup, "datatype": "Int", "indicator": "Red"},
			{"label": "Harvesters paid twice", "value": len([x for x in per_h if x["dup"] > 0]), "datatype": "Int", "indicator": "Red"},
			{"label": "To recover now (paid beyond logged)", "value": sum([x["beyond_amt"] for x in per_h]), "datatype": "Currency", "indicator": "Red"},
			{"label": "At risk at next payout", "value": tot_risk_amt, "datatype": "Currency", "indicator": "Orange"},
		]
		if view == "Duplicate Payments":
			in_range = 0
			for r in dup_rows:
				in_range = in_range + r["dup_amt"]
			summary.insert(1, {"label": "Paid twice in these dates", "value": in_range, "datatype": "Currency", "indicator": "Red"})
			result = sorted(dup_rows, key=lambda r: (str(r["date"]), r["harvester"]), reverse=True)
			columns = [
				{"fieldname": "date", "label": "Paid On", "fieldtype": "Date", "width": 100},
				{"fieldname": "payment", "label": "Coffee Payment", "fieldtype": "Link", "options": "Coffee Payment", "width": 130},
				{"fieldname": "harvester", "label": "Harvester", "fieldtype": "Link", "options": "Harvester", "width": 140},
				{"fieldname": "total_buckets", "label": "Buckets Paid", "fieldtype": "Int", "width": 105},
				{"fieldname": "offered", "label": "App Showed Unpaid", "fieldtype": "Int", "width": 140},
				{"fieldname": "logged", "label": "Logged So Far", "fieldtype": "Int", "width": 115},
				{"fieldname": "paid_before", "label": "Paid So Far", "fieldtype": "Int", "width": 100},
				{"fieldname": "dup", "label": "Paid Twice", "fieldtype": "Int", "width": 95},
				{"fieldname": "dup_amt", "label": "Paid Twice (KES)", "fieldtype": "Currency", "width": 125},
				{"fieldname": "source", "label": "Already Paid On", "fieldtype": "Data", "width": 230},
				{"fieldname": "why", "label": "Why", "fieldtype": "Data", "width": 420},
			]
		else:
			result = sorted(per_h, key=lambda r: (-r["dup_amt"], -r["risk"], r["harvester"]))
			columns = [
				{"fieldname": "harvester", "label": "Harvester", "fieldtype": "Link", "options": "Harvester", "width": 140},
				{"fieldname": "logged", "label": "Buckets Logged (Season)", "fieldtype": "Int", "width": 170},
				{"fieldname": "paid", "label": "Buckets Paid", "fieldtype": "Int", "width": 110},
				{"fieldname": "payments", "label": "Payments", "fieldtype": "Int", "width": 90},
				{"fieldname": "dup", "label": "Paid Twice", "fieldtype": "Int", "width": 95},
				{"fieldname": "dup_amt", "label": "Paid Twice (KES)", "fieldtype": "Currency", "width": 130},
				{"fieldname": "first_dup", "label": "First Paid Twice", "fieldtype": "Date", "width": 125},
				{"fieldname": "beyond", "label": "Paid Beyond Logged", "fieldtype": "Int", "width": 150},
				{"fieldname": "beyond_amt", "label": "To Recover (KES)", "fieldtype": "Currency", "width": 130},
				{"fieldname": "risk", "label": "Paid but Still Flagged Unpaid", "fieldtype": "Int", "width": 200},
				{"fieldname": "risk_amt", "label": "At Risk Next Payout (KES)", "fieldtype": "Currency", "width": 180},
			]
		data = [columns, result, None, None, summary]
	else:
		rows = frappe.db.sql("""select cp.name, cp.date, cp.harvester_id, cp.total_buckets, cp.rate,
				cp.total_payment, coalesce(d.b, 0) as day_b, cp.harvest_log_names
			from `tabCoffee Payment` cp
			left join (select harvester_id, date, sum(bucket_count) as b from `tabHarvest Log`
				where date between %(f)s and %(t)s group by harvester_id, date) d
				on d.harvester_id = cp.harvester_id and d.date = cp.date
			where cp.docstatus = 1 and cp.date between %(f)s and %(t)s""" + extra + """
			order by cp.date desc, cp.harvester_id""", params, as_dict=True)

		total_paid = 0
		back_paid = 0
		back_count = 0
		for r in rows:
			r.day_paid_b = r.total_buckets if r.total_buckets < r.day_b else r.day_b
			r.back_b = (r.total_buckets or 0) - r.day_paid_b
			r.back_amt = frappe.utils.flt(r.back_b) * frappe.utils.flt(r.rate)
			total_paid = total_paid + frappe.utils.flt(r.total_payment)
			if r.back_b > 0:
				back_paid = back_paid + r.back_amt
				back_count = back_count + 1

		summary = [
			{"label": "Paid out", "value": total_paid, "datatype": "Currency", "indicator": "Blue"},
			{"label": "For buckets picked on the pay day", "value": total_paid - back_paid, "datatype": "Currency", "indicator": "Green"},
			{"label": "Backdated", "value": back_paid, "datatype": "Currency", "indicator": "Orange"},
			{"label": "Backdated payments", "value": back_count, "datatype": "Int", "indicator": "Orange"},
		]

		if view == "By Pay Day":
			harvested = {}
			for h in frappe.db.sql("select date, sum(bucket_count) from `tabHarvest Log` where date between %(f)s and %(t)s group by date", params):
				harvested[str(h[0])] = frappe.utils.flt(h[1])
			days = {}
			order = []
			for r in rows:
				k = str(r.date)
				if k not in days:
					days[k] = {"date": r.date, "harvested": harvested.get(k, 0), "paid_b": 0, "day_b": 0, "back_b": 0,
							   "paid": 0, "back_amt": 0, "back_n": 0}
					order.append(k)
				d = days[k]
				d["paid_b"] = d["paid_b"] + (r.total_buckets or 0)
				d["day_b"] = d["day_b"] + r.day_paid_b
				d["back_b"] = d["back_b"] + r.back_b
				d["paid"] = d["paid"] + frappe.utils.flt(r.total_payment)
				d["back_amt"] = d["back_amt"] + r.back_amt
				if r.back_b > 0:
					d["back_n"] = d["back_n"] + 1
			result = []
			for k in order:
				d = days[k]
				over = d["paid_b"] - d["harvested"]
				d["over"] = over if over > 0 else 0
				result.append(d)
			columns = [
				{"fieldname": "date", "label": "Pay Day", "fieldtype": "Date", "width": 110},
				{"fieldname": "harvested", "label": "Buckets Harvested", "fieldtype": "Float", "precision": 0, "width": 140},
				{"fieldname": "paid_b", "label": "Buckets Paid", "fieldtype": "Float", "precision": 0, "width": 120},
				{"fieldname": "over", "label": "Paid More Than Harvested", "fieldtype": "Float", "precision": 0, "width": 190},
				{"fieldname": "day_b", "label": "Paid for That Day", "fieldtype": "Float", "precision": 0, "width": 140},
				{"fieldname": "back_b", "label": "Backdated Buckets", "fieldtype": "Float", "precision": 0, "width": 140},
				{"fieldname": "back_n", "label": "Backdated Payments", "fieldtype": "Int", "width": 150},
				{"fieldname": "paid", "label": "Paid Out", "fieldtype": "Currency", "width": 120},
				{"fieldname": "back_amt", "label": "Backdated Amount", "fieldtype": "Currency", "width": 140},
			]
		else:
			back = [r for r in rows if r.back_b > 0]
			names = []
			for r in back:
				r.logs = json.loads(r.harvest_log_names) if r.harvest_log_names else []
				names = names + r.logs
			logd = {}
			if names:
				for l in frappe.get_all("Harvest Log", filters={"name": ["in", names]}, fields=["name", "date"]):
					logd[l.name] = l.date
			result = []
			for r in back:
				earlier = sorted(set([logd[n] for n in r.logs if n in logd and logd[n] < r.date]))
				result.append({
					"date": r.date, "payment": r.name, "harvester": r.harvester_id,
					"total_buckets": r.total_buckets, "day_b": r.day_paid_b, "back_b": r.back_b, "rate": r.rate,
					"total_payment": r.total_payment, "back_amt": r.back_amt,
					"harvested_on": ", ".join([frappe.utils.formatdate(x, "dd MMM") for x in earlier]) if earlier else "not recorded",
					"days_late": frappe.utils.date_diff(r.date, earlier[0]) if earlier else None,
				})
			columns = [
				{"fieldname": "date", "label": "Paid On", "fieldtype": "Date", "width": 105},
				{"fieldname": "payment", "label": "Coffee Payment", "fieldtype": "Link", "options": "Coffee Payment", "width": 130},
				{"fieldname": "harvester", "label": "Harvester", "fieldtype": "Link", "options": "Harvester", "width": 140},
				{"fieldname": "total_buckets", "label": "Buckets Paid", "fieldtype": "Int", "width": 110},
				{"fieldname": "day_b", "label": "Picked on Pay Day", "fieldtype": "Int", "width": 140},
				{"fieldname": "back_b", "label": "Backdated Buckets", "fieldtype": "Int", "width": 140},
				{"fieldname": "harvested_on", "label": "Backdated Buckets Picked On", "fieldtype": "Data", "width": 200},
				{"fieldname": "days_late", "label": "Days Late", "fieldtype": "Int", "width": 90},
				{"fieldname": "rate", "label": "Rate", "fieldtype": "Currency", "width": 80},
				{"fieldname": "total_payment", "label": "Paid", "fieldtype": "Currency", "width": 100},
				{"fieldname": "back_amt", "label": "Backdated Amount", "fieldtype": "Currency", "width": 140},
			]
		data = [columns, result, None, None, summary]
	return data
