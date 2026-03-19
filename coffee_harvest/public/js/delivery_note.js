/* Coffee Harvest — Delivery Note client-side helpers */

frappe.ui.form.on("Delivery Note", {
	custom_delivery_type(frm) {
		if (frm.doc.custom_delivery_type === "Coffee Dispatch") {
			frm.set_value("custom_business_unit", "Endebess Coffee");
			frm.set_value("custom_farm", "Endebess");
			frm.set_value("custom_location", "Endebess");
			frm.set_value("set_warehouse", "Coffee Clean Warehouse - KL");
		}
	},
});

// When a line item's outturn reference or item_code changes, auto-set batch_no
frappe.ui.form.on("Delivery Note Item", {
	custom_outturn_number(frm, cdt, cdn) {
		_set_batch_from_outturn(frm, cdt, cdn);
	},
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		// Only auto-set batch on Coffee Dispatch DNs
		if (frm.doc.custom_delivery_type === "Coffee Dispatch" && row.custom_outturn_number) {
			_set_batch_from_outturn(frm, cdt, cdn);
		}
	},
	custom_no_of_bags(frm, cdt, cdn) {
		_calc_weight(frm, cdt, cdn);
	},
	custom_no_of_pockets(frm, cdt, cdn) {
		_calc_weight(frm, cdt, cdn);
	},
});

function _set_batch_from_outturn(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.custom_outturn_number || !row.item_code) return;
	const batch_id = row.custom_outturn_number + "-" + row.item_code;
	frappe.db.exists("Batch", batch_id).then((exists) => {
		if (exists) {
			frappe.model.set_value(cdt, cdn, "batch_no", batch_id);
		}
	});
}

function _calc_weight(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const bags = row.custom_no_of_bags || 0;
	const pockets = row.custom_no_of_pockets || 0;
	if (bags || pockets) {
		frappe.model.set_value(cdt, cdn, "qty", bags * 60 + pockets);
	}
}
