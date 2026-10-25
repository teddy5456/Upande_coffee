// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.ui.form.on("Coffee Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Send Daily Report Now"), () =>
			frappe.call({
				method: "upande_coffee.api.dailyreport.send_now",
				freeze: true,
				callback: (r) => {
					const out = r.message || {};
					frappe.msgprint(out.sent
						? __("Sent to {0}.", [out.recipients.join(", ")])
						: __("Not sent: {0}.", [out.reason]));
				},
			})
		);
	},
});
