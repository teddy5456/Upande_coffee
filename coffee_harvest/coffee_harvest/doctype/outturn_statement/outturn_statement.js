frappe.ui.form.on("Outturn Statement", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && !frm.doc.linked_delivery_note) {
			const label =
				frm.doc._booking_is_internal
					? __("Create Delivery Note")
					: __("Create Delivery Note");

			frm.add_custom_button(
				__("Delivery Note"),
				function () {
					frappe.call({
						method: "coffee_harvest.coffee_harvest.doctype.outturn_statement.outturn_statement.create_delivery_note",
						args: { outturn_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Creating Delivery Note..."),
						callback(r) {
							if (r.message) {
								frm.reload_doc();
								frappe.set_route("Form", "Delivery Note", r.message);
							}
						},
					});
				},
				__("Create")
			);
		}
	},
});
