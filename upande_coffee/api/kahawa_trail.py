# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Small app-support endpoints for the Kahawa Trail mobile app that don't
belong to any single doctype."""

import frappe


@frappe.whitelist()
def kahawa_my_roles():
    """The logged-in user's roles.

    User.roles is permlevel 1 on the core User doctype, readable only by
    System Manager -- frappe.client.get on a non-admin's own User document
    silently drops the roles child table from the response (even though
    it's their own record), so every non-manager always got back []. Has
    Role is a plain child table with zero DocPerm rows of its own, so it
    can't be listed directly either (frappe.client.get_list on it 403s for
    everyone, including System Manager) -- ignore_permissions sidesteps
    that since we're deliberately scoping the query to the caller's own
    user and nothing else.
    """
    roles = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "parenttype": "User"},
        pluck="role",
        ignore_permissions=True,
    )
    return {"roles": roles}
