// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.ui.form.on("Harvester Label Print", {
	refresh(frm) {
		render_hint(frm);

		if (frm.doc.labels && frm.doc.labels.length) {
			frm.add_custom_button(__("Print Labels"), () => open_print(frm), __("Actions"));
			frm.page.set_primary_action(__("Print Labels"), () => open_print(frm));
		} else {
			frm.page.set_primary_action(__("Generate Labels"), () => frm.save());
		}
	},

	action(frm) {
		render_hint(frm);
	},

	after_save(frm) {
		if (frm.__generating) {
			return;
		}
		frm.__generating = true;
		frappe.call({
			method: "coffee_harvest.coffee_harvest.label_generation.generate_labels",
			args: { label_doc_name: frm.doc.name },
			freeze: true,
			freeze_message: __("Generating harvester QR labels..."),
			callback: (r) => {
				frm.__generating = false;
				frm.reload_doc().then(() => {
					if (r.message && r.message.count) {
						frappe.show_alert({
							message: __("{0} label(s) generated. Opening print view...", [r.message.count]),
							indicator: "green",
						});
						open_print(frm);
					}
				});
			},
			error: () => {
				frm.__generating = false;
			},
		});
	},
});

function preferred_format(frm) {
	return frm.doc.action === "Empty QR Range"
		? "Harvester QR - Blank"
		: "Harvester QR - Employee Card";
}

function open_print(frm) {
	const format = preferred_format(frm);
	frappe.set_route("print", frm.doc.doctype, frm.doc.name, { format: format });
}

function render_hint(frm) {
	const format = preferred_format(frm);
	const html = `<div style="padding:8px 10px;border-left:3px solid #d7a96b;background:#fdf6ee;
		border-radius:4px;color:#6D4C41;font-size:12px;">
		Save to generate labels, then print with the <b>${frappe.utils.escape_html(format)}</b> format.
		</div>`;
	if (frm.fields_dict.print_hint) {
		frm.fields_dict.print_hint.$wrapper.html(html);
	}
}
