import frappe

def create_get_active_season_api():
    if frappe.db.exists("Server Script", "Get Active Coffee Season"):
        return "already_exists"
    s = frappe.new_doc("Server Script")
    s.name = "Get Active Coffee Season"
    s.script_type = "API"
    s.api_method = "get_active_coffee_season"
    s.allow_guest = 0
    s.disabled = 0
    s.module = "Coffee Harvest"
    s.script = (
        "rows = frappe.get_all(\n"
        "    'Coffee Season',\n"
        "    filters={'is_active': 1},\n"
        "    fields=['name','season_name','start_date','end_date','default_bucket_rate','target_cherry_kg','cafe_certified','ra_certified','notes'],\n"
        "    limit=1,\n"
        ")\n"
        "frappe.response['message'] = rows[0] if rows else None\n"
    )
    s.insert(ignore_permissions=True)
    frappe.db.commit()
    return s.name
