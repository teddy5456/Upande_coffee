// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.query_reports["Backdated Coffee Payments"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.get_today() },
		{ fieldname: "view", label: __("View"), fieldtype: "Select", reqd: 1,
			options: "Backdated Payments\nBy Pay Day\nDuplicate Payments\nHarvester Summary", default: "Backdated Payments" },
		{ fieldname: "harvester", label: __("Harvester"), fieldtype: "Link", options: "Harvester" },
	],
};
