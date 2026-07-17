app_name = "upande_coffee"
app_title = "Upande Coffee"
app_publisher = "Upande"
app_description = "Coffee farm operations: harvest, drying, outturn and payments"
app_email = "teddy@upande.com"
app_license = "mit"
required_apps = ["erpnext", "upande_core"]

# Fixtures
# --------
fixtures = [
	{
		"doctype": "Parchment Type",
		"filters": [["name", "in", ["P1", "P2", "P3", "PL", "Mbuni", "Naturals", "Estate Cured"]]],
	},
	{
		"doctype": "Workflow State",
		"filters": [["name", "in", ["Pending Weigh Approval", "Weighed", "Received"]]],
	},
	{
		"doctype": "Workflow Action Master",
		"filters": [["name", "in", ["Submit for Weighing", "Approve Weight", "Send Back", "Receive"]]],
	},
	{
		"doctype": "Workflow",
		"filters": [["name", "in", ["Harvest Pickup Flow"]]],
	},
]

# Installation / migration
# ------------------------
after_install = "upande_coffee.setup.after_install"
after_migrate = "upande_coffee.custom_fields.create_coffee_custom_fields"

# DocType JS shipped from the app (no site Client Scripts)
app_include_js = "/assets/upande_coffee/js/coffee_selling.js?v=3"
doctype_js = {
	"Delivery Note": "public/js/delivery_note.js",
	"Sales Invoice": "public/js/sales_invoice.js",
}

# Document Events
# ---------------
doc_events = {
	"Harvest Pickup": {
		"on_submit": "upande_coffee.upande_coffee.doctype.harvest_pickup.harvest_pickup.on_submit_create_stock_entry",
		"on_cancel": "upande_coffee.upande_coffee.doctype.harvest_pickup.harvest_pickup.on_cancel_reverse_stock_entry",
	},
	"Drying Assignment": {
		"on_submit": "upande_coffee.upande_coffee.doctype.drying_assignment.drying_assignment.on_submit_create_repack",
		"on_cancel": "upande_coffee.upande_coffee.doctype.drying_assignment.drying_assignment.on_cancel_reverse_repack",
	},
	"Booking": {
		"on_submit": "upande_coffee.upande_coffee.doctype.booking.booking.on_submit_transfer_to_mill",
		"on_cancel": "upande_coffee.upande_coffee.doctype.booking.booking.on_cancel_reverse_transfer",
	},
	"Outturn Statement": {
		"on_submit": "upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement.on_submit_create_milled_stock",
		"on_cancel": "upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement.on_cancel_reverse_milled_stock",
	},
	"Delivery Note": {
		"before_validate": "upande_coffee.selling_hooks.calculate_item_weights",
		"validate": "upande_coffee.selling_hooks.validate_outturn_limits",
	},
	"Sales Invoice": {
		"before_validate": "upande_coffee.selling_hooks.calculate_item_weights",
		"validate": "upande_coffee.selling_hooks.validate_outturn_limits",
	},
}

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "upande_coffee",
# 		"logo": "/assets/upande_coffee/logo.png",
# 		"title": "Upande Coffee",
# 		"route": "/upande_coffee",
# 		"has_permission": "upande_coffee.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/upande_coffee/css/upande_coffee.css"
# app_include_js = "/assets/upande_coffee/js/upande_coffee.js"

# include js, css files in header of web template
# web_include_css = "/assets/upande_coffee/css/upande_coffee.css"
# web_include_js = "/assets/upande_coffee/js/upande_coffee.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "upande_coffee/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "upande_coffee/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "upande_coffee.utils.jinja_methods",
# 	"filters": "upande_coffee.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "upande_coffee.install.before_install"
# after_install = "upande_coffee.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "upande_coffee.uninstall.before_uninstall"
# after_uninstall = "upande_coffee.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "upande_coffee.utils.before_app_install"
# after_app_install = "upande_coffee.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "upande_coffee.utils.before_app_uninstall"
# after_app_uninstall = "upande_coffee.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "upande_coffee.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "upande_coffee.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"upande_coffee.tasks.all"
# 	],
# 	"daily": [
# 		"upande_coffee.tasks.daily"
# 	],
# 	"hourly": [
# 		"upande_coffee.tasks.hourly"
# 	],
# 	"weekly": [
# 		"upande_coffee.tasks.weekly"
# 	],
# 	"monthly": [
# 		"upande_coffee.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "upande_coffee.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "upande_coffee.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "upande_coffee.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "upande_coffee.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["upande_coffee.utils.before_request"]
# after_request = ["upande_coffee.utils.after_request"]

# Job Events
# ----------
# before_job = ["upande_coffee.utils.before_job"]
# after_job = ["upande_coffee.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"upande_coffee.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

