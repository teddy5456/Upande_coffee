// Copyright (c) 2026, Upande Ltd and contributors
// For license information, please see license.txt

frappe.query_reports["Coffee Dispatch Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
			reqd: 0,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 0,
		},
		{
			fieldname: "scope",
			label: __("Scope"),
			fieldtype: "Select",
			options: ["All", "Endebess Only", "No Endebess"].join("\n"),
			default: "All",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "grade",
			label: __("Grade"),
			fieldtype: "Select",
			options: [
				"",
				"AA", "AB", "C", "PB", "E", "T", "TT",
				"MH", "ML", "NH", "NL", "HE",
				"UG", "UG1", "UG2",
			].join("\n"),
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "source" && data) {
			if (data.source === "Endebess") {
				value = `<span style="color:#b8651c;font-weight:600">${data.source}</span>`;
			} else if (data.source === "Outgrower") {
				value = `<span style="color:#2d6a3f;font-weight:600">${data.source}</span>`;
			}
		}
		return value;
	},
};
