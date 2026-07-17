// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt
//
// Sales Order coffee behaviour — Endebess grower-only.
//
// Isolation contract: for any Sales Order whose business_unit does NOT
// contain "endebess", THIS FILE IS A NO-OP. It must never mutate the doc,
// call the server, or leave visible UI. The Endebess tab is force-hidden
// on refresh regardless of Frappe's Tab Break depends_on evaluation, so
// non-Endebess users see zero difference from vanilla ERPNext.

frappe.provide("upande_coffee.endebess");

upande_coffee.endebess = {
	BAG_KG: 60,
	_cfg: null,

	// All the fields inside the Endebess tab — used to force-hide as a group.
	TAB_FIELDS: [
		"custom_endebess_tab",
		"custom_endebess_intake_sec",
		"custom_grower_code",
		"custom_expected_bags",
		"custom_expected_parchment_weight_kg",
		"custom_endebess_col_break",
		"custom_transport_expenses",
		"custom_source_booking",
		"custom_endebess_parchment_sec",
		"custom_parchment_types",
	],

	is_endebess(frm) {
		const bu = (frm.doc.business_unit || frm.doc.custom_business_unit || "");
		return bu.toLowerCase().includes("endebess");
	},

	/** Force the Endebess tab hidden for non-Endebess SOs.
	 *  We set both depends_on (Frappe) AND hidden (belt-and-braces) via
	 *  set_df_property so it works across Frappe versions where Tab Break
	 *  depends_on has quirks. */
	toggle_tab_visibility(frm) {
		const show = this.is_endebess(frm);
		for (const fname of this.TAB_FIELDS) {
			if (frm.get_field(fname)) {
				frm.set_df_property(fname, "hidden", show ? 0 : 1);
			}
		}
		frm.refresh_fields();
	},

	async _load_config() {
		if (this._cfg) return this._cfg;
		const r = await frappe.db.get_doc("Coffee Settings", "Coffee Settings");
		this._cfg = {
			price_list: r.endebess_price_list || null,
			bag_kg:     r.bag_weight_kg || this.BAG_KG,
			items: {
				milling:   r.endebess_milling_item   || null,
				handling:  r.endebess_handling_item  || null,
				transport: r.endebess_transport_item || null,
			},
		};
		return this._cfg;
	},

	async _get_rate(item_code, price_list) {
		if (!item_code || !price_list) return null;
		// Use get_list — get_value's response shape for filter-dict lookups
		// varies across Frappe versions; get_list is stable. We don't filter
		// on `selling` because some sites store Item Prices without that flag
		// set on the row; the price_list itself already carries selling=1.
		const rows = await frappe.db.get_list("Item Price", {
			filters: { item_code, price_list },
			fields:  ["price_list_rate"],
			limit:   1,
		});
		if (rows && rows.length && rows[0].price_list_rate != null) {
			return parseFloat(rows[0].price_list_rate);
		}
		return null;
	},

	async _resolve_rate(frm, item_code, fallback_list) {
		const preferred = frm.doc.selling_price_list;
		if (preferred) {
			const r = await this._get_rate(item_code, preferred);
			if (r != null) return r;
		}
		return await this._get_rate(item_code, fallback_list);
	},

	async _item_meta(item_code) {
		// Fetch what the item_code trigger would normally populate for us.
		// Bypassing that trigger to preserve rate means we have to fill
		// item_name / description / uom ourselves.
		if (!item_code) return { stock_uom: "Nos", item_name: item_code, description: item_code };
		const r = await frappe.db.get_value(
			"Item",
			item_code,
			["stock_uom", "item_name", "description", "item_group"],
		);
		const m = (r && r.message) || {};
		return {
			stock_uom:   m.stock_uom   || "Nos",
			item_name:   m.item_name   || item_code,
			description: m.description || m.item_name || item_code,
			item_group:  m.item_group  || null,
		};
	},

	async refill_service_items(frm) {
		if (!this.is_endebess(frm)) return;                  // ← isolation guard
		if (frm.doc.docstatus !== 0) return;
		if (frm.__endebess_refilling) return;
		frm.__endebess_refilling = true;

		try {
			const cfg = await this._load_config();
			const owned = new Set(Object.values(cfg.items).filter(Boolean));
			if (!owned.size) return;

			const weight = parseFloat(frm.doc.custom_expected_parchment_weight_kg || 0);
			// Drop (a) old service rows so we can rebuild, and (b) any
			// blank rows — ERPNext auto-appends an empty row 1 on new
			// SOs that would otherwise fail the mandatory-field check.
			frm.doc.items = (frm.doc.items || []).filter(
				r => r.item_code && !owned.has(r.item_code)
			);

			if (weight <= 0) {
				frm.refresh_field("items");
				return;
			}

			const milling_qty   = weight / 1000;
			const handling_qty  = weight / cfg.bag_kg;
			const transport_qty = frm.doc.custom_transport_expenses ? handling_qty : 0;

			const specs = [];
			if (cfg.items.milling)   specs.push({ code: cfg.items.milling,   qty: milling_qty });
			if (cfg.items.handling)  specs.push({ code: cfg.items.handling,  qty: handling_qty });
			if (cfg.items.transport && transport_qty > 0) {
				specs.push({ code: cfg.items.transport, qty: transport_qty });
			}

			for (const s of specs) {
				const [rate, meta] = await Promise.all([
					this._resolve_rate(frm, s.code, cfg.price_list),
					this._item_meta(s.code),
				]);
				console.debug(
					"[Endebess] rate lookup", s.code,
					"→ price_list=" + (cfg.price_list || frm.doc.selling_price_list),
					"→ rate=" + rate,
				);

				// Use frappe.model.add_child (not frm.add_child) — the latter
				// fires ERPNext's item_code trigger which asynchronously calls
				// get_item_details and OVERWRITES our rate. Model-level add +
				// Object.assign leaves our values intact.
				const row = frappe.model.add_child(frm.doc, "Sales Order Item", "items");
				const effective_rate = rate != null ? rate : 0;
				const qty = Math.max(0.01, parseFloat(s.qty.toFixed(3)));
				Object.assign(row, {
					item_code:         s.code,
					item_name:         meta.item_name,
					qty,
					uom:               meta.stock_uom,
					stock_uom:         meta.stock_uom,
					rate:              effective_rate,
					price_list_rate:   effective_rate,
					amount:            qty * effective_rate,
					conversion_factor: 1,
					description:       rate == null
						? `${meta.description} — no rate; set in ${cfg.price_list || 'Coffee Settings ▸ Endebess Price List'}`
						: meta.description,
				});
			}

			frm.refresh_field("items");
			frm.trigger("calculate_taxes_and_totals");
		} finally {
			frm.__endebess_refilling = false;
		}
	},
};

/** Sum every row of custom_parchment_types into the top-level
 *  Expected Bags + Expected Parchment Weight fields, then trigger the
 *  service-items refill so Milling / Handling / Transport rows recalc.
 *  Child-table = source of truth; manual edits to the top fields get
 *  overwritten when a parchment-types row changes. */
upande_coffee.endebess.roll_up_parchment_totals = function (frm) {
	if (!this.is_endebess(frm)) return;
	const rows = frm.doc.custom_parchment_types || [];
	if (!rows.length) return;
	let total_bags = 0;
	let total_weight = 0;
	for (const r of rows) {
		total_bags   += parseFloat(r.expected_bags || 0);
		total_weight += parseFloat(r.expected_weight_kg || 0);
	}
	frm.set_value("custom_expected_bags", Math.round(total_bags));
	frm.set_value("custom_expected_parchment_weight_kg", parseFloat(total_weight.toFixed(2)));
	// refill runs off the weight-change handler above.
};

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		// FIRST — enforce isolation. Non-Endebess SOs see the tab hidden.
		upande_coffee.endebess.toggle_tab_visibility(frm);
	},

	business_unit(frm) {
		upande_coffee.endebess.toggle_tab_visibility(frm);
		upande_coffee.endebess.refill_service_items(frm);
	},

	custom_business_unit(frm) {
		upande_coffee.endebess.toggle_tab_visibility(frm);
		upande_coffee.endebess.refill_service_items(frm);
	},

	custom_expected_parchment_weight_kg(frm) {
		upande_coffee.endebess.refill_service_items(frm);
	},

	custom_transport_expenses(frm) {
		upande_coffee.endebess.refill_service_items(frm);
	},

	selling_price_list(frm) {
		upande_coffee.endebess._cfg = null;
		upande_coffee.endebess.refill_service_items(frm);
	},
});

// Child-table handlers: recompute the top-level totals whenever a
// parchment-types row is added, edited, or removed. `expected_bags` /
// `expected_weight_kg` changes fire on cell blur; add/delete fires on
// grid mutation.
//
// Guard on frm.doctype — the same child (Endebess Parchment Type) is
// mounted on Work Order too, and work_order.js registers its own set of
// handlers on the child. Without this guard both handlers fire for every
// row change on either parent.
function _so_roll(frm) {
	if (frm.doctype !== "Sales Order") return;
	upande_coffee.endebess.roll_up_parchment_totals(frm);
}
frappe.ui.form.on("Endebess Parchment Type", {
	expected_bags:                     _so_roll,
	expected_weight_kg:                _so_roll,
	parchment_type:                    _so_roll,
	custom_parchment_types_add:        _so_roll,
	custom_parchment_types_remove:     _so_roll,
});
