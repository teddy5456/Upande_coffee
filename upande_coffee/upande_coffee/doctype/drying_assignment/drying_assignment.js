frappe.ui.form.on("Drying Assignment", {
	full_batch_final_weight(frm) {
		if (
			frm.doc.removal_mode === "Full Batch" &&
			!(frm.doc.full_batch_bin_entries && frm.doc.full_batch_bin_entries.length) &&
			frm.doc.full_batch_final_weight > 0 &&
			frm.doc.full_batch_target_bin
		) {
			_mark_completed(frm);
		}
	},

	full_batch_target_bin(frm) {
		if (
			frm.doc.removal_mode === "Full Batch" &&
			!(frm.doc.full_batch_bin_entries && frm.doc.full_batch_bin_entries.length) &&
			frm.doc.full_batch_final_weight > 0 &&
			frm.doc.full_batch_target_bin
		) {
			_mark_completed(frm);
		}
	},
});

frappe.ui.form.on("Full Batch Bin Entry", {
	weight_kg(frm) {
		_sync_bin_entries_total(frm);
	},
	target_bin(frm) {
		_check_bin_entries_complete(frm);
	},
	full_batch_bin_entries_remove(frm) {
		_sync_bin_entries_total(frm);
	},
});

frappe.ui.form.on("Drying Table Removal", {
	final_weight_kg(frm) {
		if (frm.doc.removal_mode === "Per Table") _check_per_table_complete(frm);
	},
	target_bin(frm) {
		if (frm.doc.removal_mode === "Per Table") _check_per_table_complete(frm);
	},
	table_removals_remove(frm) {
		if (frm.doc.removal_mode === "Per Table") _check_per_table_complete(frm);
	},
});

frappe.ui.form.on("Drying Type Removal", {
	final_weight_kg(frm) {
		if (frm.doc.removal_mode === "Per Coffee Type") _check_per_type_complete(frm);
	},
	target_bin(frm) {
		if (frm.doc.removal_mode === "Per Coffee Type") _check_per_type_complete(frm);
	},
	type_removals_remove(frm) {
		if (frm.doc.removal_mode === "Per Coffee Type") _check_per_type_complete(frm);
	},
});

function _sync_bin_entries_total(frm) {
	const rows = frm.doc.full_batch_bin_entries || [];
	const total = rows.reduce((s, r) => s + (r.weight_kg || 0), 0);
	if (total > 0) {
		frm.set_value("full_batch_final_weight", total);
	}
	_check_bin_entries_complete(frm);
}

function _check_bin_entries_complete(frm) {
	const rows = frm.doc.full_batch_bin_entries || [];
	if (rows.length > 0 && rows.every((r) => r.weight_kg > 0 && r.target_bin)) {
		_mark_completed(frm);
	}
}

function _mark_completed(frm) {
	if (frm.doc.docstatus !== 0) return;
	frm.set_value("drying_status", "Completed");
	frm.set_value("completed_drying", 1);
	if (!frm.doc.end_date) {
		frm.set_value("end_date", frappe.datetime.get_today());
	}
}

function _check_per_table_complete(frm) {
	const rows = frm.doc.table_removals || [];
	if (rows.length > 0 && rows.every((r) => r.final_weight_kg > 0 && r.target_bin)) {
		_mark_completed(frm);
	}
}

function _check_per_type_complete(frm) {
	const rows = frm.doc.type_removals || [];
	if (rows.length > 0 && rows.every((r) => r.final_weight_kg > 0 && r.target_bin)) {
		_mark_completed(frm);
	}
}
