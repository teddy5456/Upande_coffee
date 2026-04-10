/* Coffee Harvest — Delivery Note client-side logic
 *
 * Coffee Dispatch behaviour:
 *  - When custom_delivery_type = "Coffee Dispatch", auto-fill farm / BU / location
 *  - Items grid gains extra Link fields: Outturn Statement, Grower (filtered to growers only)
 *  - Outturn selection on an item auto-sets batch_no and can pre-fill bags/pockets
 *  - Bags + pockets auto-calculate qty
 */

const COFFEE_DISPATCH = "Coffee Dispatch";

// ── Header ────────────────────────────────────────────────────────────────────

frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		_setup_item_queries(frm);

		// Button: fill grades from the header outturn (Endebess dispatch helper)
		if (
			frm.doc.custom_delivery_type === COFFEE_DISPATCH &&
			frm.doc.custom_outturn_references &&
			frm.doc.docstatus === 0
		) {
			frm.add_custom_button(__("Fill Grades from Outturn"), () =>
				_fill_grades_from_outturn(frm, frm.doc.custom_outturn_references)
			);
		}
	},

	custom_delivery_type(frm) {
		if (frm.doc.custom_delivery_type === COFFEE_DISPATCH) {
			frm.set_value("custom_business_unit", "Endebess Coffee");
			frm.set_value("custom_farm", "Endebess");
			frm.set_value("custom_location", "Endebess");
			// Use setTimeout to run after fetch_from has fired so we win the race
			setTimeout(() => frm.set_value("set_warehouse", "Coffee Clean Warehouse - KL"), 300);
		}
		_setup_item_queries(frm);
	},

	custom_outturn_references(frm) {
		// When the header outturn changes, offer to fill grades
		if (frm.doc.custom_outturn_references && frm.doc.docstatus === 0) {
			frappe.confirm(
				__("Fill line items from outturn {0}?", [frm.doc.custom_outturn_references]),
				() => _fill_grades_from_outturn(frm, frm.doc.custom_outturn_references)
			);
		}
	},
});

// ── Item rows ─────────────────────────────────────────────────────────────────

frappe.ui.form.on("Delivery Note Item", {
	custom_outturn_number(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.custom_outturn_number && row.item_code) {
			_set_batch(cdt, cdn, row.custom_outturn_number, row.item_code);
		}
	},

	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (
			frm.doc.custom_delivery_type === COFFEE_DISPATCH &&
			row.custom_outturn_number &&
			row.item_code
		) {
			_set_batch(cdt, cdn, row.custom_outturn_number, row.item_code);
		}
	},

	custom_no_of_bags(frm, cdt, cdn) {
		_calc_qty(cdt, cdn);
	},

	custom_no_of_pockets(frm, cdt, cdn) {
		_calc_qty(cdt, cdn);
	},
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function _setup_item_queries(frm) {
	if (frm.doc.custom_delivery_type !== COFFEE_DISPATCH) return;

	// Outturn Statement filter: only submitted records
	frm.fields_dict.items.grid.get_field("custom_outturn_number").get_query =
		function () {
			return { filters: { docstatus: 1 } };
		};

	// Grower filter: only customers who have at least one Booking
	frm.fields_dict.items.grid.get_field("custom_grower").get_query = function () {
		return {
			query:
				"coffee_harvest.coffee_harvest.delivery_note.get_grower_customers",
		};
	};
}

function _set_batch(cdt, cdn, outturn, item_code) {
	const batch_id = outturn + "-" + item_code;
	frappe.db.exists("Batch", batch_id).then((exists) => {
		if (exists) {
			frappe.model.set_value(cdt, cdn, "batch_no", batch_id);
		}
	});
}

function _calc_qty(cdt, cdn) {
	const row = locals[cdt][cdn];
	const bags = row.custom_no_of_bags || 0;
	const pockets = row.custom_no_of_pockets || 0;
	if (bags || pockets) {
		frappe.model.set_value(cdt, cdn, "qty", bags * 60 + pockets);
	}
}

function _fill_grades_from_outturn(frm, outturn_name) {
	frappe.call({
		method: "coffee_harvest.coffee_harvest.delivery_note.get_available_outturn_grades",
		args: { outturn_name },
		freeze: true,
		freeze_message: __("Loading grades from outturn..."),
		callback(r) {
			if (!r.message || !r.message.length) {
				frappe.msgprint(__("No grade rows found on outturn {0}.", [outturn_name]));
				return;
			}
			r.message.forEach((grade) => {
				const row = frm.add_child("items");
				frappe.model.set_value(row.doctype, row.name, {
					item_code: grade.item_code,
					uom: "Kilogram",
					custom_outturn_number: outturn_name,
					custom_no_of_bags: grade.no_of_bags,
					custom_no_of_pockets: grade.no_of_pockets,
					qty: grade.net_weight,
					batch_no: grade.batch_id || "",
					warehouse: "Coffee Clean Warehouse - KL",
				});
			});
			frm.refresh_field("items");
			frappe.show_alert(
				{
					message: __("{0} grade rows added.", [r.message.length]),
					indicator: "green",
				},
				4
			);
		},
	});
}
