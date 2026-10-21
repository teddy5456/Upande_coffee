// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

function cbp_totals(frm) {
	let buckets = 0;
	(frm.doc.items || []).forEach((r) => {
		buckets += r.bucket_count || 0;
		frappe.model.set_value(r.doctype, r.name, "amount", (r.bucket_count || 0) * (frm.doc.rate || 0));
	});
	frm.set_value("total_buckets", buckets);
	frm.set_value("total_payment", buckets * (frm.doc.rate || 0));
}

function cbp_get_days(frm) {
	if (!frm.doc.harvester_id) {
		frappe.msgprint(__("Pick the harvester first."));
		return;
	}
	frappe.call({
		method: "upande_coffee.upande_coffee.doctype.coffee_backdated_payment.coffee_backdated_payment.get_unpaid_logs",
		args: { harvester_id: frm.doc.harvester_id, date: frm.doc.date },
		freeze: true,
	}).then((r) => {
		const d = r.message || {};
		if (!frm.doc.rate && d.rate) frm.set_value("rate", d.rate);
		frm.clear_table("items");
		(d.logs || []).forEach((l) => {
			const row = frm.add_child("items");
			row.harvest_log = l.name;
			row.log_date = l.date;
			row.block = l.block;
			row.bucket_count = l.bucket_count;
		});
		frm.refresh_field("items");
		cbp_totals(frm);
		if (!(d.logs || []).length) {
			frappe.msgprint(__("{0} has no unpaid buckets before {1}.", [frm.doc.harvester_id, frm.doc.date]));
		}
	});
}

frappe.ui.form.on("Coffee Backdated Payment", {
	refresh(frm) {
		frm.remove_custom_button(__("Get Unpaid Days"));
		if (frm.doc.docstatus === 0) frm.add_custom_button(__("Get Unpaid Days"), () => cbp_get_days(frm));
		frm.set_query("harvest_log", "items", () => ({
			filters: { harvester_id: frm.doc.harvester_id, paid: 0, date: ["<", frm.doc.date] },
		}));
	},
	harvester_id(frm) {
		if (frm.doc.harvester_id) cbp_get_days(frm);
	},
	date(frm) {
		if (frm.doc.harvester_id) cbp_get_days(frm);
	},
	rate(frm) {
		cbp_totals(frm);
	},
});

frappe.ui.form.on("Coffee Backdated Payment Item", {
	items_remove(frm) {
		cbp_totals(frm);
	},
	bucket_count(frm) {
		cbp_totals(frm);
	},
});
