// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt
//
// Shared coffee selling helpers for Delivery Note and Sales Invoice.
// Gated on the Business Unit name containing "endebess".
//
// Row flow: pick an outturn -> bags/pockets/qty prefill with what REMAINS of
// that grade on the outturn; the user then adjusts bags + pockets (qty = bags
// x bag weight + pockets kg) and cannot exceed the remaining quantity.

frappe.provide("upande_coffee.selling");

upande_coffee.selling = {
	bag_kg: 60,
	limits: {}, // row name -> remaining kg for the picked outturn grade

	init() {
		frappe.db.get_single_value("Coffee Settings", "bag_weight_kg").then((v) => {
			if (v) this.bag_kg = v;
		});
	},

	is_coffee(frm) {
		const bu = frm.doc.business_unit || frm.doc.custom_business_unit || "";
		return bu.toLowerCase().includes("endebess");
	},

	setup_outturn_query(frm) {
		if (!this.is_coffee(frm)) return;
		const field = frm.fields_dict.items.grid.get_field("custom_outturn_number");
		if (field) {
			field.get_query = () => ({ filters: { docstatus: 1 } });
		}
	},

	_adding: false,

	prefill_row(frm, cdt, cdn) {
		if (this._adding) return;
		const row = locals[cdt][cdn];
		if (!this.is_coffee(frm) || !row.custom_outturn_number || !row.item_code) return;

		frappe.call({
			method: "upande_coffee.api.deliverynoteapi.get_outturn_grade",
			args: {
				outturn_name: row.custom_outturn_number,
				item_code: row.item_code,
				doctype: frm.doc.doctype,
				exclude_doc: frm.doc.name,
			},
			callback: (r) => {
				const g = r.message || {};
				if (!g.total_kg) {
					frappe.msgprint(
						__("Outturn {0} has no grade row for item {1}.", [
							row.custom_outturn_number,
							row.item_code,
						])
					);
					return;
				}
				if (g.bag_kg) this.bag_kg = g.bag_kg;
				this.limits[cdn] = g.remaining_kg;
				frappe.model.set_value(cdt, cdn, {
					custom_no_of_bags: g.remaining_bags,
					custom_no_of_pockets: g.remaining_pockets,
					qty: g.remaining_kg,
					uom: "Kilogram",
					batch_no: g.batch_id || "",
				});
				frappe.show_alert(
					{
						message: __("Grade {0}: {1} kg remaining on {2}", [
							g.grade,
							g.remaining_kg,
							row.custom_outturn_number,
						]),
						indicator: "blue",
					},
					5
				);
			},
		});
	},

	/* "Get Items From → Outturn Statement": pick grades + quantities from an
	   outturn; rows append with batch, bags/pockets and outturn reference. */
	open_outturn_picker(frm) {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: __("Get Items from Outturn"),
			fields: [
				{
					fieldtype: "Link",
					fieldname: "outturn",
					label: __("Outturn Statement"),
					options: "Outturn Statement",
					reqd: 1,
					get_query: () => ({ filters: { docstatus: 1 } }),
					onchange() {
						const ot = d.get_value("outturn");
						if (ot) self._load_picker_rows(frm, d, ot);
					},
				},
				{ fieldtype: "HTML", fieldname: "grades" },
			],
			primary_action_label: __("Add Items"),
			primary_action() {
				const wrap = d.get_field("grades").$wrapper;
				const outturn = d.get_value("outturn");
				const picked = [];
				wrap.find("tr[data-item]").each(function () {
					const tr = $(this);
					if (!tr.find(".ot-pick").prop("checked")) return;
					const bags = parseFloat(tr.find(".ot-bags").val()) || 0;
					const pockets = parseFloat(tr.find(".ot-pockets").val()) || 0;
					const qty = bags * self.bag_kg + pockets;
					if (qty <= 0) return;
					picked.push({ item: tr.data("item"), batch: tr.data("batch") || "",
						bags: bags, pockets: pockets, qty: qty });
				});
				if (!picked.length) {
					frappe.show_alert({ message: __("Nothing selected — tick the grades to add."), indicator: "orange" }, 4);
					return;
				}
				d.hide();
				self._adding = true;
				(async () => {
					try {
						for (const p of picked) {
							const row = frm.add_child("items");
							// item_code first and awaited: ERPNext's item fetch resets
							// qty/uom/batch, so our values must land afterwards
							await frappe.model.set_value(row.doctype, row.name, "item_code", p.item);
							await frappe.model.set_value(row.doctype, row.name, {
								qty: p.qty,
								uom: "Kilogram",
								custom_outturn_number: outturn,
								custom_no_of_bags: p.bags,
								custom_no_of_pockets: p.pockets,
								batch_no: p.batch,
							});
						}
					} finally {
						self._adding = false;
					}
					frm.refresh_field("items");
					frappe.show_alert(
						{ message: __("{0} item(s) added from outturn {1}.", [picked.length, outturn]), indicator: "green" }, 4
					);
				})();
			},
		});
		d.show();
	},

	_load_picker_rows(frm, d, outturn) {
		const self = this;
		frappe.call({
			method: "upande_coffee.api.deliverynoteapi.get_outturn_items",
			args: { outturn_name: outturn, doctype: frm.doc.doctype, exclude_doc: frm.doc.name },
			callback(r) {
				const rows = r.message || [];
				if (rows.length && rows[0].bag_kg) self.bag_kg = rows[0].bag_kg;
				const body = rows.map((g) => {
					const disabled = g.remaining_kg <= 0 ? "disabled" : "";
					return `<tr data-item="${frappe.utils.escape_html(g.item_code)}" data-batch="${g.batch_id || ""}">
						<td><input type="checkbox" class="ot-pick" ${disabled}></td>
						<td>${frappe.utils.escape_html(g.grade)}</td>
						<td class="text-right">${g.remaining_kg} / ${g.total_kg}</td>
						<td><input type="number" class="ot-bags form-control input-sm" style="width:80px"
							value="${g.remaining_bags}" min="0" ${disabled}></td>
						<td><input type="number" class="ot-pockets form-control input-sm" style="width:80px"
							value="${g.remaining_pockets}" min="0" step="0.1" ${disabled}></td>
					</tr>`;
				}).join("");
				d.get_field("grades").$wrapper.html(`
					<table class="table table-bordered" style="margin-top:10px">
						<thead><tr><th></th><th>${__("Grade")}</th><th class="text-right">${__("Remaining / Total kg")}</th>
						<th>${__("Bags")}</th><th>${__("Pockets (kg)")}</th></tr></thead>
						<tbody>${body || `<tr><td colspan="5">${__("No grade rows on this outturn.")}</td></tr>`}</tbody>
					</table>`);
			},
		});
	},

	calc_qty(frm, cdt, cdn) {
		if (this._adding) return;
		const row = locals[cdt][cdn];
		const qty = (row.custom_no_of_bags || 0) * this.bag_kg + (row.custom_no_of_pockets || 0);
		if (!qty) return;

		const limit = this.limits[cdn];
		if (limit != null && qty > limit + 0.01) {
			frappe.msgprint(
				__("Only {0} kg of this grade remain on outturn {1} — quantity reset to the maximum.", [
					limit,
					row.custom_outturn_number,
				])
			);
			const bags = Math.floor(limit / this.bag_kg);
			frappe.model.set_value(cdt, cdn, {
				custom_no_of_bags: bags,
				custom_no_of_pockets: flt(limit - bags * this.bag_kg, 2),
				qty: limit,
			});
			return;
		}
		frappe.model.set_value(cdt, cdn, "qty", qty);
	},
};

upande_coffee.selling.init();
