import frappe


def get_context(context):
	"""Coffee production page — login-gated, never cached.

	Mirrors coffee_dashboard.py: the CSRF token is handed to the page so its
	POSTs to the whitelisted API methods are accepted, and caching is disabled so
	a plain reload always shows the current forecast rather than this morning's.
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/coffee-production"
		raise frappe.Redirect
	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()
	if getattr(frappe.local, "response_headers", None) is not None:
		frappe.local.response_headers["Cache-Control"] = "no-store, must-revalidate"
