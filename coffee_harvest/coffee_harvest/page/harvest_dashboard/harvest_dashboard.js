frappe.pages["harvest-dashboard"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Harvest Dashboard",
		single_column: true,
	});
	new HarvestDashboard(page);
};

class HarvestDashboard {
	constructor(page) {
		this.page = page;
		this.$main = $(page.main);
		this._setup_filters();
		this._setup_body();
	}

	_setup_filters() {
		this.start_field = this.page.add_field({
			fieldtype: "Date",
			label: "Season Start",
			fieldname: "start_date",
			reqd: 1,
			change: () => {},
		});
		this.end_field = this.page.add_field({
			fieldtype: "Date",
			label: "Season End",
			fieldname: "end_date",
			reqd: 1,
			change: () => {},
		});

		const today = frappe.datetime.get_today();
		const year = parseInt(today.slice(0, 4));
		const month = parseInt(today.slice(5, 7));
		const seasonStart = (month >= 10 ? year : year - 1) + "-10-01";
		this.start_field.set_input(seasonStart);
		this.end_field.set_input(today);

		this.page.add_button(__("Load"), () => this.load(), {
			btn_class: "btn-primary",
		});
	}

	_setup_body() {
		this.$body = $(
			'<div style="padding:var(--padding-lg, 20px)"></div>'
		).appendTo(this.$main);
		this.$body.html(
			'<p class="text-muted">Set the season date range above and click <strong>Load</strong>.</p>'
		);
	}

	load() {
		const start = this.start_field.get_value();
		const end = this.end_field.get_value();
		if (!start || !end) {
			frappe.msgprint(__("Please set both Season Start and Season End dates."));
			return;
		}
		this.$body.html(
			'<div class="text-center" style="padding:60px"><i class="fa fa-spinner fa-spin fa-2x text-muted"></i></div>'
		);
		frappe.call({
			method: "coffee_harvest.coffee_harvest.dashboard_api.get_harvest_block_summary",
			args: { start_date: start, end_date: end },
			callback: (r) => this._render(r.message || [], start, end),
		});
	}

	_render(rows, start, end) {
		if (!rows.length) {
			this.$body.html(
				'<p class="text-muted" style="padding:20px">No harvest data found for this period.</p>'
			);
			return;
		}

		const fmt = (n, dec = 0) =>
			Number(n || 0).toLocaleString(undefined, {
				minimumFractionDigits: dec,
				maximumFractionDigits: dec,
			});

		const tot = rows.reduce(
			(a, r) => ({
				harvest_days: Math.max(a.harvest_days, r.harvest_days || 0),
				buckets: a.buckets + (r.total_buckets || 0),
				cherry: a.cherry + (r.total_cherry_kg || 0),
				cost: a.cost + (r.estimated_cost || 0),
			}),
			{ harvest_days: 0, buckets: 0, cherry: 0, cost: 0 }
		);

		const totalCostPerKg = tot.cherry > 0 ? (tot.cost / tot.cherry).toFixed(1) : "—";

		// ── Summary cards ────────────────────────────────────────────────────
		const summaryCards = [
			{ label: "Season", value: `${start.slice(0,7)} → ${end.slice(0,7)}`, color: "#6c757d" },
			{ label: "Total Cherry", value: fmt(tot.cherry, 1) + " kg", color: "#2d6a3f" },
			{ label: "Total Buckets", value: fmt(tot.buckets), color: "#2d6a3f" },
			{ label: "Labour Cost", value: "KES " + fmt(tot.cost), color: "#e67e22" },
			{ label: "KES / kg Cherry", value: totalCostPerKg, color: "#e67e22" },
		].map(c => `
			<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 16px;text-align:center">
				<div style="color:${c.color};font-size:20px;font-weight:700">${c.value}</div>
				<div style="color:#888;font-size:11px;margin-top:4px;text-transform:uppercase;letter-spacing:.5px">${c.label}</div>
			</div>`).join("");

		// ── Table rows ───────────────────────────────────────────────────────
		const bodyRows = rows.map(r => `
			<tr>
				<td><strong>${frappe.utils.escape_html(r.block)}</strong></td>
				<td class="text-right">${r.harvest_days}</td>
				<td class="text-right">${fmt(r.total_buckets)}</td>
				<td class="text-right">${fmt(r.total_cherry_kg, 1)}</td>
				<td class="text-right">${fmt(r.estimated_cost)}</td>
				<td class="text-right">${parseFloat(r.cost_per_kg || 0).toFixed(1)}</td>
			</tr>`).join("");

		this.$body.html(`
			<!-- Summary strip -->
			<div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px">
				${summaryCards}
			</div>

			<!-- Table card -->
			<div class="card shadow-sm" style="margin-bottom:20px">
				<div class="card-header d-flex justify-content-between align-items-center py-2">
					<span class="font-weight-bold">Harvest &amp; Labour Cost per Block</span>
					<small class="text-muted">Season: ${start} → ${end}</small>
				</div>
				<div class="card-body p-0">
					<div class="table-responsive">
						<table class="table table-sm table-bordered mb-0" style="font-size:13px">
							<thead class="thead-light">
								<tr>
									<th>Block</th>
									<th class="text-right">Harvest Days</th>
									<th class="text-right">Buckets</th>
									<th class="text-right">Cherry (kg)</th>
									<th class="text-right">Labour Cost (KES)</th>
									<th class="text-right">KES / kg Cherry</th>
								</tr>
							</thead>
							<tbody>${bodyRows}</tbody>
							<tfoot>
								<tr style="font-weight:600;background:#f0f4f8">
									<td>Season Total</td>
									<td></td>
									<td class="text-right">${fmt(tot.buckets)}</td>
									<td class="text-right">${fmt(tot.cherry, 1)}</td>
									<td class="text-right">${fmt(tot.cost)}</td>
									<td class="text-right">${totalCostPerKg}</td>
								</tr>
							</tfoot>
						</table>
					</div>
				</div>
			</div>

			<!-- Charts row -->
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px">
				<div class="card shadow-sm">
					<div class="card-header py-2"><span class="font-weight-bold">Cherry (kg) per Block</span></div>
					<div class="card-body" id="hd-cherry-chart" style="height:260px"></div>
				</div>
				<div class="card shadow-sm">
					<div class="card-header py-2"><span class="font-weight-bold">Labour Cost (KES) per Block</span></div>
					<div class="card-body" id="hd-cost-chart" style="height:260px"></div>
				</div>
			</div>

			<!-- Navigation links -->
			<div style="display:flex;gap:8px;flex-wrap:wrap">
				<a href="/app/query-report/Block%20Seasonal%20Performance" class="btn btn-sm btn-default">
					<i class="fa fa-table mr-1"></i> Block Seasonal Performance
				</a>
				<a href="/app/query-report/Cherry%20to%20Clean%20Conversion" class="btn btn-sm btn-default">
					<i class="fa fa-exchange mr-1"></i> Cherry → Clean Conversion
				</a>
				<a href="/app/query-report/Outgrower%20Coffee%20Report" class="btn btn-sm btn-default">
					<i class="fa fa-users mr-1"></i> Outgrower Coffee Report
				</a>
			</div>
		`);

		// ── Render charts using Frappe Charts ────────────────────────────────
		this._render_chart(
			"#hd-cherry-chart",
			rows.map(r => frappe.utils.escape_html(r.block)),
			[{ name: "Cherry (kg)", values: rows.map(r => parseFloat(r.total_cherry_kg || 0)) }],
			"#2d6a3f"
		);
		this._render_chart(
			"#hd-cost-chart",
			rows.map(r => frappe.utils.escape_html(r.block)),
			[{ name: "Labour Cost (KES)", values: rows.map(r => parseFloat(r.estimated_cost || 0)) }],
			"#e67e22"
		);
	}

	_render_chart(selector, labels, datasets, color) {
		const el = this.$body.find(selector)[0];
		if (!el || !labels.length) return;
		try {
			new frappe.Chart(el, {
				data: { labels, datasets },
				type: "bar",
				height: 230,
				colors: [color],
				axisOptions: { xIsSeries: 0 },
				tooltipOptions: { formatTooltipY: d => Number(d).toLocaleString() },
			});
		} catch (e) {
			// frappe.Chart may not be available in all builds
			el.innerHTML = `<p class="text-muted text-center" style="padding-top:80px">Chart unavailable</p>`;
		}
	}
}
