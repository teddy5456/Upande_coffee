// Output Weight is derived from the grade breakdown (see outturn_statement.py
// _calculate_grade_weights) -- but that only runs on save. Without this, the
// read-only Output Weight field just sits frozen while you type bags/pockets,
// which looks exactly like the auto-sum stopped working.
function outturn_bag_kg(frm) {
	return frm.coffee_bag_kg || 60;
}

function outturn_recalc_row(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const net = (row.no_of_bags || 0) * outturn_bag_kg(frm) + (row.no_of_pockets || 0);
	frappe.model.set_value(cdt, cdn, "net_weight", net).then(() => outturn_recalc_total(frm));
}

function outturn_recalc_total(frm) {
	const total = (frm.doc.table_cyvh || []).reduce((sum, r) => sum + (r.net_weight || 0), 0);
	frm.set_value("output_weight", total);
}

frappe.ui.form.on("Outturn Details", {
	no_of_bags(frm, cdt, cdn) {
		outturn_recalc_row(frm, cdt, cdn);
	},
	no_of_pockets(frm, cdt, cdn) {
		outturn_recalc_row(frm, cdt, cdn);
	},
	table_cyvh_remove(frm) {
		outturn_recalc_total(frm);
	},
});

// Grower/outturn_number/parchment_weight are fetched from the linked Sales
// Order server-side in _fetch_parchment_weight() -- but only on save. With no
// client-side equivalent, they sit blank while filling out a new Normal
// outturn, which looks exactly like "grower isn't being fetched" until you
// save and reload.
function outturn_fetch_from_so(frm) {
	if (!frm.doc.custom_source_sales_order || frm.doc.outturn_type === "Super Outturn") return;
	frappe.db.get_value("Sales Order", frm.doc.custom_source_sales_order, ["customer", "custom_outturn_number"]).then(({ message: so }) => {
		if (!so) return;
		if (!frm.doc.grower && so.customer) frm.set_value("grower", so.customer);
		if (!frm.doc.outturn_number && so.custom_outturn_number) frm.set_value("outturn_number", so.custom_outturn_number);
	});
	if (!frm.doc.parchment_weight) {
		frappe.db.get_list("Endebess Parchment Type", {
			filters: { parent: frm.doc.custom_source_sales_order, parenttype: "Sales Order" },
			fields: ["expected_weight_kg"],
			limit: 0,
		}).then((rows) => {
			const total = (rows || []).reduce((sum, r) => sum + (r.expected_weight_kg || 0), 0);
			if (total) frm.set_value("parchment_weight", total);
		});
	}
}

frappe.ui.form.on("Outturn Statement", {
	custom_source_sales_order(frm) {
		outturn_fetch_from_so(frm);
	},
	onload(frm) {
		frappe.db.get_single_value("Coffee Settings", "bag_weight_kg").then((v) => {
			frm.coffee_bag_kg = v || 60;
		});
		// Grade is a plain Link -> Item with no filter, so it listed every Item
		// in the company (packaging, inputs, everything) instead of just the
		// coffee grades (Coffee Grade AA, AB, ... -- item_group "Coffee-Grades").
		frm.set_query("grade", "table_cyvh", () => ({
			filters: { item_group: "Coffee-Grades" },
		}));
		// Covers a doc that already arrived with custom_source_sales_order set
		// (e.g. created via a list-view action) before the user touches anything.
		if (frm.doc.__islocal) outturn_fetch_from_so(frm);
	},
	// Create > Delivery Note bills an EXTERNAL grower for what was actually
	// milled, received and transported, at their Sales Order rates; jute bags
	// added to the note leave the store. Estate (internal) outturns get no button.
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.custom_source_sales_order) return;
		const dn = frm.doc.linked_delivery_note;
		Promise.all([
			frappe.db.get_value("Sales Order", frm.doc.custom_source_sales_order, "customer"),
			dn ? frappe.db.get_value("Delivery Note", dn, "docstatus") : Promise.resolve(null),
		]).then(([so, d]) => {
			const customer = so && so.message && so.message.customer;
			if (d && d.message && d.message.docstatus < 2) {
				frm.add_custom_button(__("Delivery Note"), () => frappe.set_route("Form", "Delivery Note", dn), __("View"));
				return;
			}
			if (!customer) return;
			frappe.db.get_value("Customer", customer, "is_internal_customer").then((c) => {
				if (c && c.message && c.message.is_internal_customer) return;
				frm.add_custom_button(__("Delivery Note"), () => {
					frappe.call({
						method: "upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement.create_delivery_note",
						args: { outturn_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Creating Delivery Note..."),
					}).then((r) => {
						if (r.message) frappe.set_route("Form", "Delivery Note", r.message);
					});
				}, __("Create"));
			});
		});
	},
});
