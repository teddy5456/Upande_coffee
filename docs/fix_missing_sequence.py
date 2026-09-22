"""Diagnose and repair 'Unknown SEQUENCE: <doctype>_id_seq'.

Run:  echo "exec(open('fix_sequence.py').read(), globals())" | bench --site <site> console

This needs a shell on the server. The desk cannot do it: System Console and Server
Scripts run under safe_exec, which exposes read-only SQL and no DDL. Saving the
DocType will not do it either — Frappe only creates the sequence when the table
itself is created (frappe/database/mariadb/schema.py), so no bench migrate will
bring a lost one back.

If only a MariaDB client is available, this is the whole repair (start value must
be above the highest existing name, and mind the site's own database):

    create sequence if not exists harvester_id_seq start <max_name + 1> nocache nocycle;

Background: when a DocType is named by Autoincrement, Frappe asks MariaDB for the
next value from a SEQUENCE object called <doctype>_id_seq. If that object is
missing, every insert dies with error 4091.

The usual reason is a restore. MariaDB's mysqldump does not carry SEQUENCE
objects the way it carries tables, so a site rebuilt from a plain dump keeps all
its rows and loses its sequences — and only breaks later, on the first insert.

This script does not guess. It reports what the doctype is actually set to, then
repairs only what is broken, and starts the sequence above the highest existing
row so it can never collide with a name already in use.
"""

DOCTYPE = "Harvester"          # change if another doctype is affected
SLUG = "_id_seq"

from frappe.utils import cint

seq = frappe.scrub(DOCTYPE + SLUG)

meta = frappe.db.get_value("DocType", DOCTYPE, ["autoname", "naming_rule"], as_dict=True)
print(f"doctype        : {DOCTYPE}")
print(f"  autoname     : {meta.autoname!r}")
print(f"  naming_rule  : {meta.naming_rule!r}")

setters = frappe.get_all(
    "Property Setter",
    filters={"doc_type": DOCTYPE, "property": ("in", ["autoname", "naming_rule"])},
    fields=["name", "property", "value"],
)
if setters:
    print("  Customize Form has overridden naming:")
    for s in setters:
        print(f"    {s.property} = {s.value!r}   (Property Setter {s.name})")

exists = frappe.db.sql(
    """SELECT COUNT(*) FROM information_schema.TABLES
       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
    seq,
)[0][0]
print(f"  sequence     : {seq} {'present' if exists else 'MISSING'}")

autoincrement = (meta.naming_rule == "Autoincrement") or (meta.autoname == "autoincrement")

if not autoincrement:
    print(
        f"\nThis site does NOT name {DOCTYPE} by autoincrement, so it should never ask\n"
        f"for {seq}. If the app you are hitting still errors, the site raising it is a\n"
        f"DIFFERENT site from this one — check the server URL in the app's Settings."
    )
else:
    # Highest numeric name in use. Autoincrement names are plain integers, but a
    # site that changed naming schemes part-way can hold non-numeric names too,
    # so those are ignored rather than crashing the repair.
    rows = frappe.db.sql(f"SELECT name FROM `tab{DOCTYPE}`")
    highest = 0
    non_numeric = 0
    for (nm,) in rows:
        try:
            highest = max(highest, int(nm))
        except (TypeError, ValueError):
            non_numeric += 1
    print(f"  rows         : {len(rows)} ({non_numeric} with non-numeric names)")
    print(f"  highest id   : {highest}")

    start = highest + 1
    if exists:
        print(f"\nSequence already exists — nothing to create. Ensuring it is past {highest}.")
        frappe.db.set_next_sequence_val(DOCTYPE, value=start)
    else:
        print(f"\nCreating {seq} starting at {start} so it cannot collide with an existing name.")
        frappe.db.create_sequence(DOCTYPE, check_not_exists=True, start_value=start)
    frappe.db.commit()

    after = frappe.db.sql(
        """SELECT COUNT(*) FROM information_schema.TABLES
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        seq,
    )[0][0]
    print(f"  sequence now : {'present' if after else 'STILL MISSING'}")
    print("REPAIRED" if after else "REPAIR FAILED")
