// Coffee Intake — form behaviour.
//
// On Sales Order pick: fetch the SO's parchment types (via the server
// helper) and populate the items table with one row per type. Operator
// then adjusts actual received quantities per row before submit.

frappe.ui.form.on("Coffee Intake", {
	refresh(frm) {
		frm.set_query("sales_order", () => ({
			filters: {
				custom_outturn_number: ["is", "set"],
				docstatus: ["!=", 2],
			},
		}));
	},

	sales_order(frm) {
		if (!frm.doc.sales_order) return;
		frappe.call({
			method: "upande_coffee.upande_coffee.doctype.coffee_intake.coffee_intake.get_intake_rows_from_sales_order",
			args: { sales_order: frm.doc.sales_order },
			callback(r) {
				const rows = r.message || [];
				// Clear existing rows and repopulate from the SO. Operator can
				// edit qty per row afterwards; the source_warehouse comes from
				// the SO's parchment-types table (Endebess Parchment Type).
				frm.doc.items = [];
				for (const spec of rows) {
					const row = frm.add_child("items", {
						parchment_type:   spec.parchment_type,
						qty_kg:           spec.qty_kg,
						source_warehouse: spec.source_warehouse,
					});
				}
				frm.refresh_field("items");
				if (!rows.length) {
					frappe.show_alert({
						message: __("This SO has no parchment types set — add rows manually."),
						indicator: "orange",
					}, 6);
				}
			},
		});
	},
});
