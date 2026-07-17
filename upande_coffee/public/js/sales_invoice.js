// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt
//
// Sales Invoice coffee behaviour — see coffee_selling.js. Gated on the
// Business Unit name containing "endebess"; other business units unaffected.

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		upande_coffee.selling.setup_outturn_query(frm);
		if (upande_coffee.selling.is_coffee(frm) && frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Outturn Statement"),
				() => upande_coffee.selling.open_outturn_picker(frm),
				__("Get Items From")
			);
		}
	},

	business_unit(frm) {
		upande_coffee.selling.setup_outturn_query(frm);
		frm.refresh();
	},

	custom_business_unit(frm) {
		upande_coffee.selling.setup_outturn_query(frm);
		frm.refresh();
	},
});

frappe.ui.form.on("Sales Invoice Item", {
	custom_outturn_number(frm, cdt, cdn) {
		upande_coffee.selling.prefill_row(frm, cdt, cdn);
	},
	custom_no_of_bags(frm, cdt, cdn) {
		upande_coffee.selling.calc_qty(frm, cdt, cdn);
	},
	custom_no_of_pockets(frm, cdt, cdn) {
		upande_coffee.selling.calc_qty(frm, cdt, cdn);
	},
});
