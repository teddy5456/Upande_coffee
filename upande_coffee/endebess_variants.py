"""Endebess (outgrower) grade variants.

Installs:
  - Item Attribute "Grower Type" with values Internal (INT) and Outgrower (OG).
  - For each grade in outturn_statement.GRADE_ITEM_MAP, a companion `<grade>-OG`
    Item with is_stock_item=1, valuation_rate=0, allow_zero_valuation_rate=1.
    Zero-valuation lets outgrower stock flow through Material Receipt → Delivery
    Note without hitting our COGS.

Rationale for companion items (vs proper template + variants): existing grade
items in production carry stock ledger entries, so promoting them to a template
via `has_variants=1` would require renaming them and re-pointing every historic
transaction — risky. The companion-item approach yields the same operational
outcome (a distinct outgrower `AA-OG` item at zero valuation) and preserves the
Internal/Outgrower separation via the Grower Type attribute on both items. When
a future site has a clean data set, run `promote_to_templates()` to convert to
proper variants; the -OG naming stays stable.

Run:
    bench --site <site> execute upande_coffee.endebess_variants.run

Also wired into after_migrate via hooks.py.
"""

import frappe


ATTRIBUTE_NAME = "Grower Type"
ATTRIBUTE_VALUES = [
	{"attribute_value": "Internal",  "abbr": "INT"},
	{"attribute_value": "Outgrower", "abbr": "OG"},
]


def _grade_codes():
	"""Read the canonical grade map from outturn_statement so this stays in
	sync with milling / dispatch code paths."""
	from upande_coffee.upande_coffee.doctype.outturn_statement.outturn_statement import (
		GRADE_ITEM_MAP,
	)
	return list(dict.fromkeys(GRADE_ITEM_MAP.values()))


def og_item_code(base_item_code):
	"""Public helper: given a base grade code (e.g. 'AA'), return the outgrower
	variant code ('AA-OG'). Suffix comes from Coffee Settings so ops can
	change it (or turn the feature off by clearing the field)."""
	if not base_item_code:
		return base_item_code
	suffix = frappe.db.get_single_value("Coffee Settings", "endebess_og_suffix") or "-OG"
	if base_item_code.endswith(suffix):
		return base_item_code
	return f"{base_item_code}{suffix}"


# ─────────────────────────────────────────────────────────────────────────
# Item Attribute
# ─────────────────────────────────────────────────────────────────────────

def _ensure_grower_type_attribute():
	if not frappe.db.exists("Item Attribute", ATTRIBUTE_NAME):
		doc = frappe.get_doc({
			"doctype":       "Item Attribute",
			"attribute_name": ATTRIBUTE_NAME,
			"item_attribute_values": ATTRIBUTE_VALUES,
		})
		doc.insert(ignore_permissions=True)
		print(f"  item attribute created: {ATTRIBUTE_NAME}")
		return

	# Make sure both values exist even if the attribute is pre-existing
	# (e.g. from a partial earlier install).
	doc = frappe.get_doc("Item Attribute", ATTRIBUTE_NAME)
	existing = {v.attribute_value for v in (doc.item_attribute_values or [])}
	changed = False
	for v in ATTRIBUTE_VALUES:
		if v["attribute_value"] not in existing:
			doc.append("item_attribute_values", v)
			changed = True
	if changed:
		doc.save(ignore_permissions=True)
		print(f"  item attribute values patched: {ATTRIBUTE_NAME}")
	else:
		print(f"  item attribute exists: {ATTRIBUTE_NAME}")


# ─────────────────────────────────────────────────────────────────────────
# -OG companion items
# ─────────────────────────────────────────────────────────────────────────

def _ensure_og_item(base_code):
	og_code = og_item_code(base_code)
	if frappe.db.exists("Item", og_code):
		print(f"  og item exists: {og_code}")
		return

	# Copy the base item's UOM + item group if the base exists; otherwise
	# use safe defaults (Kilogram + Coffee grades group, else All Item Groups).
	base = None
	if frappe.db.exists("Item", base_code):
		base = frappe.db.get_value(
			"Item", base_code,
			["stock_uom", "item_group", "item_name", "description"],
			as_dict=True,
		)

	stock_uom = (base and base.get("stock_uom")) or "Kilogram"
	item_group = (base and base.get("item_group")) or _pick_grade_group()
	item_name = f"{(base and base.get('item_name')) or base_code} (Outgrower)"
	description = (
		f"Outgrower variant of {base_code}. Zero valuation — passes through our "
		f"stock ledger without contributing to COGS. Attach Grower Type = "
		f"Outgrower on any downstream automation."
	)

	item = frappe.get_doc({
		"doctype":                    "Item",
		"item_code":                  og_code,
		"item_name":                  item_name,
		"item_group":                 item_group,
		"stock_uom":                  stock_uom,
		"is_stock_item":              1,
		"is_sales_item":              1,
		"is_purchase_item":           0,
		"allow_zero_valuation_rate":  1,
		"valuation_rate":             0,
		"description":                description,
		"include_item_in_manufacturing": 0,
	})
	item.insert(ignore_permissions=True)
	print(f"  og item created: {og_code} (uom={stock_uom}, group={item_group})")


def _pick_grade_group():
	for candidate in ("Coffee Grades", "Coffee", "Products"):
		if frappe.db.exists("Item Group", candidate):
			return candidate
	return "All Item Groups"


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

def run():
	_ensure_grower_type_attribute()
	for base in _grade_codes():
		_ensure_og_item(base)
	frappe.db.commit()
	print("Endebess variants complete.")
