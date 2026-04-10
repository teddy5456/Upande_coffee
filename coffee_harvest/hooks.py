app_name = "coffee_harvest"
app_title = "Coffee Harvest"
app_publisher = "Upande"
app_description = "Endebess Coffee Estate Harvest & Processing System"
app_email = "dev@upande.com"
app_license = "mit"

after_install = "coffee_harvest.coffee_harvest.setup.after_install"
after_migrate = "coffee_harvest.coffee_harvest.custom_fields.create_coffee_custom_fields"

doctype_js = {
    "Delivery Note": "public/js/delivery_note.js",
}

add_to_apps_screen = [
    {
        "name": "Coffee Harvest",
        "logo": "/assets/coffee_harvest/images/logo.png",
        "title": "Coffee Harvest",
        "route": "/app/harvest-log",
    }
]

doc_events = {
    "Harvest Pickup": {
        "on_submit": "coffee_harvest.coffee_harvest.doctype.harvest_pickup.harvest_pickup.on_submit_create_stock_entry",
        "on_cancel": "coffee_harvest.coffee_harvest.doctype.harvest_pickup.harvest_pickup.on_cancel_reverse_stock_entry",
    },
    "Drying Assignment": {
        "on_submit": "coffee_harvest.coffee_harvest.doctype.drying_assignment.drying_assignment.on_submit_create_repack",
        "on_cancel": "coffee_harvest.coffee_harvest.doctype.drying_assignment.drying_assignment.on_cancel_reverse_repack",
    },
    "Booking": {
        "on_submit": "coffee_harvest.coffee_harvest.doctype.booking.booking.on_submit_transfer_to_mill",
        "on_cancel": "coffee_harvest.coffee_harvest.doctype.booking.booking.on_cancel_reverse_transfer",
    },
    "Outturn Statement": {
        "on_submit": "coffee_harvest.coffee_harvest.doctype.outturn_statement.outturn_statement.on_submit_create_milled_stock",
        "on_cancel": "coffee_harvest.coffee_harvest.doctype.outturn_statement.outturn_statement.on_cancel_reverse_milled_stock",
    },
    "Delivery Note": {
        "before_validate": "coffee_harvest.coffee_harvest.delivery_note.calculate_item_weights",
        "before_save": "coffee_harvest.coffee_harvest.delivery_note.fix_coffee_dispatch_warehouse",
    },
    "Sales Invoice": {
        "before_insert": "coffee_harvest.coffee_harvest.sales_invoice.copy_farm_from_delivery_note",
    },
}

fixtures = [
    {
        "dt": "Module Def",
        "filters": [["name", "in", ["Coffee Harvest"]]],
    },
    {
        "dt": "Custom Field",
        "filters": [["module", "=", "Coffee Harvest"]],
    },
    {
        "dt": "Print Format",
        "filters": [["module", "=", "Coffee Harvest"]],
    },
]
