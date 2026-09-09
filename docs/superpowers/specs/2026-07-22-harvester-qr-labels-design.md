# Harvester QR Labels, Sequence & Print Formats — Design

Date: 2026-07-22
App: `coffee_harvest`
Module: Coffee Harvest

## Problem / Goals

1. **Bug:** The Harvester form cannot be saved. It throws
   `Field not permitted in query: one_fm_iqama_number`.
2. Provide a tool to **print harvester QR code cards** — either a range of blank
   cards, a single employee-linked card, or a table of many employees.
3. Track the current QR number with a **Coffee QR Sequence** counter so numbering
   is centralized and never collides.
4. Provide **two print formats**, both ID/credit-card sized (CR80, 85.6×54 mm):
   - one "cool" badge for employee-linked cards (name, employee ID, harvester ID, QR),
   - one minimal card for blank/empty harvester QRs (QR + harvester ID).

## Root cause of the bug

`harvester.json` field `national_id` has `fetch_from: employee.one_fm_iqama_number`.
The `one_fm` app is not installed on this bench, so that field does not exist on
Employee. Frappe rejects the fetch query → the form save fails for every user.

The `csf_ke` app **does** add a real `national_id` Data field to Employee.

**Fix:** change the fetch source to `employee.national_id`. The Harvester field is
already named `national_id` / labelled "National ID", so it is a drop-in fix.

## Design decisions (confirmed with user)

- Reuse the **existing Harvester doctype** as the record store. The label tool
  creates/links Harvester records; it does not introduce a parallel store.
- `harvester_id` stays an **independent `HARVESTER-n` sequence**, valid whether or
  not an employee is linked. QR payload always encodes `{"harvester_id": "..."}`,
  matching what the Kahawa Trail mobile app already scans.
- Label production uses a **batch doctype + grid print format** (mirrors the
  existing `Label Print` / `QR Sequence` / `generate_id` convention in `upande_kaitet`).
- Label QR images are generated **server-side as PNG files** via the `qrcode`
  library (same as `gen_label_id.py`) — reliable for large batches. The Harvester
  form's on-screen preview keeps its current external-API render (offline is not a
  requirement).
- Employee card shows `employee_name` + employee number + harvester ID + QR.
  National ID is **not** printed on the card.

## Components

### A. Coffee QR Sequence (new Single DocType)

Single source of truth for harvester numbering. Mirrors `QR Sequence`.

Fields:
- `harvester_counter` — Int, default 0. Last `HARVESTER-n` issued.
- `last_updated` — Datetime, read-only.
- `last_updated_by` — Link (User), read-only.

Controller method `get_next(n=1)`:
- Loads the single doc, reads `base = harvester_counter or 0`.
- Sets `harvester_counter = base + n`, stamps `last_updated` / `last_updated_by`,
  saves and commits.
- Returns `base`. Issued IDs are `base+1 .. base+n` (mirrors `gen_label_id`).

### B. Harvester (existing DocType) — changes

1. `national_id.fetch_from`: `employee.one_fm_iqama_number` → `employee.national_id`.
2. `before_insert`: replace the `MAX(...)` SQL scan with
   `base = Coffee QR Sequence.get_next(1)` → `harvester_id = f"HARVESTER-{base+1}"`.
   Guarantees no collision between records created directly and via the label tool.
   Only assigns when `harvester_id` is empty (existing records untouched).

### C. Harvester Label Print (new tool DocType)

Modeled on `Label Print`. Non-submittable tool document.

Fields:
- `action` — Select: `Empty QR Range` / `Employee Card` / `Employee Table`.
- `qty` — Int (shown for `Empty QR Range`). Number of blank cards.
- `employee` — Link Employee (shown for `Employee Card`).
- `employee_rows` — Table → **Harvester Label Employee** child (shown for `Employee Table`).
- `labels` — Table → **Harvester Label Item** child (result set, read-only, drives print).
- `print_hint` — Data/HTML, read-only, tells the user which format to pick.

Child DocType **Harvester Label Employee**:
- `employee` — Link Employee.
- `employee_name` — Data, `fetch_from: employee.employee_name`, read-only.

Child DocType **Harvester Label Item**:
- `harvester_id` — Data.
- `employee` — Link Employee.
- `employee_name` — Data.
- `employee_number` — Data.
- `qr_code_image` — Data (file URL of the generated PNG).

Permissions on all three: `System Manager`, `Coffee Harvest User`.

### D. Server logic — `coffee_harvest/coffee_harvest/label_generation.py`

`@frappe.whitelist() def generate_labels(label_doc_name, action, qty=0, employee=None, employee_rows=None)`:
1. Load the Harvester Label Print doc; clear existing `labels`.
2. Resolve target harvesters per action:
   - **Empty QR Range:** create `qty` blank Harvester records (no employee). Each
     gets its `HARVESTER-n` via `before_insert`. Collect them.
   - **Employee Card:** find an existing Harvester for `employee`; create one if none.
   - **Employee Table:** repeat the Employee Card logic per row (dedup by employee).
3. For each target harvester:
   - Build payload `{"harvester_id": harvester_id}`, render a PNG with `qrcode`
     into `/files/qr_codes/`, insert a `File` doc (matches `gen_label_id.py`).
   - Append a `labels` row (harvester_id, employee, employee_name, employee_number,
     qr_code_image=file_url).
4. Save the doc, set `print_hint` to the matching format name, return a summary.

### E. Client script — `harvester_label_print.js` (in the doctype folder)

- Toggles section visibility by `action` (via `depends_on` in JSON where possible).
- `after_save`: calls `generate_labels`, reloads the doc, then offers/opens the
  print view with the format matching the action.

### F. Print Formats (2) — `doc_type = Harvester Label Print`, Jinja, standard=No

Both iterate `doc.labels` and render a CR80 grid (85.6×54 mm cards) with
`@media print` sizing and page breaks. Colours match the existing coffee-brown QR
card (`#fdf6ee` / `#4E342E` / `#d7a96b`).

1. **Harvester QR – Employee Card**: badge layout — worker `employee_name`, employee
   number, harvester ID, QR image. The "cool" ID-badge card.
2. **Harvester QR – Blank**: QR image + harvester ID only. For pre-printed stock.

The label tool defaults the print dropdown to the format matching the action; the
user can still switch.

## Data flow

```
User picks action + inputs on Harvester Label Print
      │ after_save
      ▼
generate_labels()  ──creates/links──►  Harvester records (HARVESTER-n via Coffee QR Sequence)
      │ per harvester
      ├─ qrcode → PNG → File doc
      └─ append row to doc.labels
      │
      ▼
Print Format (Employee Card | Blank) iterates doc.labels → CR80 grid → browser print
```

## Error handling

- `generate_labels` validates `action` and required inputs (qty > 0; employee set;
  non-empty table); raises `frappe.throw` with a clear message otherwise.
- Employee lookups that already have a Harvester reuse it (no duplicate harvester per
  employee).
- `Coffee QR Sequence.get_next` commits immediately to avoid race-induced collisions.

## Testing

- Bug fix: open an existing Harvester, set an employee that has `national_id`, save →
  succeeds; `national_id` populates.
- Sequence: `get_next(5)` twice yields non-overlapping ranges; counter advances by 5
  each call.
- Range mode: qty=40 creates 40 blank Harvester records with sequential IDs, 40 label
  rows, 40 PNGs; blank print format renders a 40-card grid.
- Employee mode: creates/reuses one Harvester, employee card shows name + numbers + QR.
- Table mode: N employees → N cards; duplicate employee rows dedupe.

## Out of scope

- Changing the Kahawa Trail mobile app (QR payload is unchanged).
- Physical printer/label-stock calibration beyond CR80 CSS sizing.
- Migrating the existing `upande_kaitet` QR Sequence / Label Print.

## Files touched

New (in `coffee_harvest`):
- `coffee_harvest/doctype/coffee_qr_sequence/` (+ .py)
- `coffee_harvest/doctype/harvester_label_print/` (+ .py, .js)
- `coffee_harvest/doctype/harvester_label_employee/`
- `coffee_harvest/doctype/harvester_label_item/`
- `coffee_harvest/label_generation.py`
- `coffee_harvest/print_format/harvester_qr_employee_card/`
- `coffee_harvest/print_format/harvester_qr_blank/`

Modified:
- `coffee_harvest/doctype/harvester/harvester.json` (fetch_from fix)
- `coffee_harvest/doctype/harvester/harvester.py` (before_insert → sequence)
