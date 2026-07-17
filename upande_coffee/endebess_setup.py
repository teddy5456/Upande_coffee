"""Endebess (outgrower) Sales Order infrastructure.

Idempotent installer that seeds:
  - Item Group "Coffee Services"
  - Service items: "Milling Service", "Handling Service", "Transport Service"
    (Export Bags Service comes in Phase 3 when Outturn reconciliation lands.)
  - Price List "Endebess Standard" with default rates
  - Sales Taxes and Charges Template "Endebess VAT 16% (Export Bags)"
    (installed now, applied in Phase 3)

Run after pulling this branch:

    bench --site <site> execute upande_coffee.endebess_setup.run

Called automatically from after_install / after_migrate via hooks.py.

Rates are the defaults observed on the milling invoice print format:
  Milling:   $45.00 / tonne of parchment
  Handling:   $1.50 / 60-kg bag of parchment
  Transport:  $3.00 / 60-kg bag of output (added only when SO transport toggle is on)
  Export Bags $3.50 / unit (Phase 3)
  VAT:          16 %  (Phase 3)
"""

import frappe


PRICE_LIST_NAME = "Endebess Standard"
ITEM_GROUP = "Coffee Services"
VAT_TEMPLATE_NAME = "Endebess VAT 16% (Export Bags)"


SERVICE_ITEMS = [
	{
		"item_code": "Milling Service",
		"item_name": "Milling Service",
		"description": "Coffee milling — per tonne of parchment received.",
		"stock_uom": "Tonne",
		"sales_uom": "Tonne",
		"default_rate": 45.00,
	},
	{
		"item_code": "Handling Service",
		"item_name": "Handling Service",
		"description": "Coffee handling — per 60-kg bag of parchment received.",
		"stock_uom": "Nos",
		"sales_uom": "Nos",
		"default_rate": 1.50,
	},
	{
		"item_code": "Transport Service",
		"item_name": "Transport Service",
		"description": "Transport — per 60-kg bag of milled output.",
		"stock_uom": "Nos",
		"sales_uom": "Nos",
		"default_rate": 3.00,
	},
]


def run():
	_ensure_item_group()
	_ensure_price_list()
	_ensure_uom("Tonne")
	for spec in SERVICE_ITEMS:
		_ensure_service_item(spec)
	_ensure_vat_template()
	frappe.db.commit()
	print("Endebess setup complete.")


# ─────────────────────────────────────────────────────────────────────────
# Item Group
# ─────────────────────────────────────────────────────────────────────────

def _ensure_item_group():
	if frappe.db.exists("Item Group", ITEM_GROUP):
		print(f"  item group exists: {ITEM_GROUP}")
		return
	parent = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
	doc = frappe.get_doc({
		"doctype":          "Item Group",
		"item_group_name":  ITEM_GROUP,
		"parent_item_group": parent,
		"is_group":         0,
	})
	doc.insert(ignore_permissions=True)
	print(f"  item group created: {ITEM_GROUP}")


# ─────────────────────────────────────────────────────────────────────────
# UOM
# ─────────────────────────────────────────────────────────────────────────

def _ensure_uom(uom_name):
	if frappe.db.exists("UOM", uom_name):
		return
	frappe.get_doc({
		"doctype":     "UOM",
		"uom_name":    uom_name,
		"must_be_whole_number": 0,
	}).insert(ignore_permissions=True)
	print(f"  uom created: {uom_name}")


# ─────────────────────────────────────────────────────────────────────────
# Price List
# ─────────────────────────────────────────────────────────────────────────

def _ensure_price_list():
	if frappe.db.exists("Price List", PRICE_LIST_NAME):
		print(f"  price list exists: {PRICE_LIST_NAME}")
		return
	doc = frappe.get_doc({
		"doctype":       "Price List",
		"price_list_name": PRICE_LIST_NAME,
		"currency":      "USD",
		"selling":       1,
		"buying":        0,
		"enabled":       1,
	})
	doc.insert(ignore_permissions=True)
	print(f"  price list created: {PRICE_LIST_NAME}")


# ─────────────────────────────────────────────────────────────────────────
# Service Items + Item Prices
# ─────────────────────────────────────────────────────────────────────────

def _ensure_service_item(spec):
	item_code = spec["item_code"]
	if not frappe.db.exists("Item", item_code):
		doc = frappe.get_doc({
			"doctype":       "Item",
			"item_code":     item_code,
			"item_name":     spec["item_name"],
			"item_group":    ITEM_GROUP,
			"stock_uom":     spec["stock_uom"],
			"sales_uom":     spec["sales_uom"],
			"is_stock_item": 0,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"is_service_item": 1,
			"description":   spec["description"],
			"include_item_in_manufacturing": 0,
		})
		doc.insert(ignore_permissions=True)
		print(f"  item created: {item_code}")
	else:
		print(f"  item exists: {item_code}")

	# Seed a default Item Price in the Endebess Standard list.
	existing = frappe.db.exists("Item Price", {
		"item_code":  item_code,
		"price_list": PRICE_LIST_NAME,
	})
	if existing:
		return
	ip = frappe.get_doc({
		"doctype":    "Item Price",
		"item_code":  item_code,
		"price_list": PRICE_LIST_NAME,
		"selling":    1,
		"currency":   "USD",
		"price_list_rate": spec["default_rate"],
	})
	ip.insert(ignore_permissions=True)
	print(f"  item price created: {item_code} @ {spec['default_rate']} USD")


# ─────────────────────────────────────────────────────────────────────────
# VAT Template (Phase 3 usage)
# ─────────────────────────────────────────────────────────────────────────

def _ensure_vat_template():
	"""16 % VAT scoped to Export Bags Service by item_code.
	The template ships now so Phase 3 can attach it without a second install
	step. Skips silently if the site has no default Sales Tax account."""
	if frappe.db.exists("Sales Taxes and Charges Template", VAT_TEMPLATE_NAME):
		print(f"  vat template exists: {VAT_TEMPLATE_NAME}")
		return
	company = frappe.defaults.get_global_default("company")
	if not company:
		print("  no default company — skipping VAT template (create later).")
		return
	# Try to find an existing tax account to pin the template to. If none
	# exists yet, we skip; ops can wire the account and re-run this installer.
	account = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Tax", "is_group": 0},
		"name",
	)
	if not account:
		print(f"  no Tax account under {company} — skipping VAT template.")
		return
	doc = frappe.get_doc({
		"doctype": "Sales Taxes and Charges Template",
		"title":   VAT_TEMPLATE_NAME,
		"company": company,
		"taxes": [{
			"charge_type":  "On Net Total",
			"account_head": account,
			"description":  "VAT 16% on Export Bags",
			"rate":         16.0,
		}],
	})
	doc.insert(ignore_permissions=True)
	print(f"  vat template created: {doc.name}")
