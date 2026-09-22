# Kahawa Trail — server-side install (Server Scripts + Custom Fields)

Everything the Kahawa Trail app v1.2.0 needs on the Frappe site, as paste-able
Server Scripts. **Target site: `kaitet-group.upande.com`.**

Server Scripts are used (rather than whitelisted python in `upande_coffee/api/`)
because the app calls **bare method names** — `/api/method/kahawa_submit_issue`.
Only a Server Script with `api_method` set resolves a bare name; app python would
need a dotted path (`upande_coffee.api.supportapi.submit_issue`) and therefore an
app deploy plus an app-side change. Move them into the app later if you prefer;
see "Migrating into the app" at the bottom.

Requires `server_script_enabled: 1` in the site config.

## Gotcha these scripts already work around

**Return values go on `frappe.flags`, not `frappe.response`.** `safe_exec` only
exposes `frappe.response` to a script when it is *already* non-empty
(`if frappe.response: out.frappe.response = frappe.response` in
`frappe/utils/safe_exec.py`), so assigning to it raises
`SyntaxError: Not allowed to write to object ... default_function` in any context
where it is empty. `execute_api_server_script` returns `_globals.frappe.flags`,
and `frappe.handler` assigns that dict to `frappe.response["message"]` — so
setting `frappe.flags.issue = ...` produces exactly the `{"message": {...}}`
shape the app parses, unconditionally. An earlier draft of these scripts used
`frappe.response["message"] = {...}`; it may work over HTTP but fails under
direct execution, so they all use flags now.

Verified on `core.local` (Frappe v15/v16, ERPNext installed) by calling
`frappe.get_doc("Server Script", name).execute_method()` — the same path
`frappe.handler` uses — for all four API scripts, plus an HTTP round trip
confirming the bare method names resolve.

## What to create

| # | Server Script name | Type | Key settings |
|---|---|---|---|
| 1 | Kahawa Trail - Submit Issue | API | API Method: `kahawa_submit_issue` |
| 2 | Kahawa Trail - Support Updates | API | API Method: `kahawa_support_updates` |
| 3 | Kahawa Trail - Support Reply | API | API Method: `kahawa_support_reply` |
| 4 | Kahawa Trail - Register Push Token | API | API Method: `kahawa_register_push_token` |
| 5 | Kahawa Trail - Push Support Reply | DocType Event | Reference Doctype: `Comment`, Event: `After Insert` |
| 6 | Kahawa Trail - Install Fields | API | API Method: `kahawa_install_fields` — run once, then delete |

Leave `Allow Guest` **unchecked** on all of them, and `Disabled` unchecked.
Module can be left blank or set to `Coffee Harvest`.

## Order

1. Paste **#6** first, call it once, confirm, then delete it. It creates the
   Custom Fields the rest depend on.
2. Paste **#1–#5**.
3. Smoke-test from the app: Contact Support → send a report.

---

## 6. Kahawa Trail - Install Fields  (run once, then DELETE)

Creates the Custom Fields by inserting `Custom Field` docs one at a time —
deliberately **not** via `create_custom_fields()`, because reaching that helper
from a Server Script needs `frappe.get_attr`, which is not reliably in the
`safe_exec` whitelist. `frappe.new_doc` is. Skips anything already present, so
re-running is harmless.

**Why the fields matter**, in case you are tempted to skip this:

- `client_ref` on Harvest Log is the **duplicate guard**. When an upload times
  out, the app cannot know whether the insert committed. It looks this ref up
  before retrying and adopts the existing log instead of logging the bucket
  twice. Without the field that lookup errors, the app falls back to inserting,
  and a timed-out-but-committed scan can double-count. Indexed, because the
  lookup runs on every retried row.
- `gps_*` on Harvest Log store where the scan happened. Without them the app
  still logs fine; Frappe just drops the unknown keys silently.
- `custom_expo_push_tokens` on User is where device push tokens live. Without it,
  push registration fails and notifications fall back to the app's own polling.

```python
# ===========================================================================
# Kahawa Trail - Install Fields   (TEMPORARY — delete after one successful run)
# ---------------------------------------------------------------------------
# Endpoint: POST /api/method/kahawa_install_fields
# Args: none
#
# Inserts Custom Field docs directly rather than calling create_custom_fields(),
# which would need frappe.get_attr — not reliably whitelisted in safe_exec.
# Idempotent: existing fields are skipped, so calling this twice is safe.
# ===========================================================================

if "System Manager" not in frappe.get_roles(frappe.session.user):
    frappe.throw("Only a System Manager may install fields")

fields = {
    "Harvest Log": [
        {
            # Idempotency key from the app: "deviceId:queueRowId". Indexed
            # because the retry-dedupe lookup filters on it.
            "fieldname": "client_ref",
            "fieldtype": "Data",
            "label": "Client Ref",
            "insert_after": "bucket_count",
            "read_only": 1,
            "no_copy": 1,
            "search_index": 1,
        },
        {
            "fieldname": "gps_section",
            "fieldtype": "Section Break",
            "label": "Scan Location",
            "insert_after": "client_ref",
            "collapsible": 1,
        },
        {
            "fieldname": "gps_latitude",
            "fieldtype": "Float",
            "label": "Latitude",
            "precision": "6",
            "insert_after": "gps_section",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "gps_longitude",
            "fieldtype": "Float",
            "label": "Longitude",
            "precision": "6",
            "insert_after": "gps_latitude",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "gps_accuracy_m",
            "fieldtype": "Float",
            "label": "GPS Accuracy (m)",
            "precision": "1",
            "insert_after": "gps_longitude",
            "read_only": 1,
            "no_copy": 1,
        },
    ],
    "User": [
        {
            # Newline-separated Expo push tokens, one line per device.
            "fieldname": "custom_expo_push_tokens",
            "fieldtype": "Small Text",
            "label": "Expo Push Tokens",
            "insert_after": "mute_sounds",
            "read_only": 1,
            "no_copy": 1,
        },
    ],
}

created = []
skipped = []

for dt in fields:
    for f in fields[dt]:
        cf_name = dt + "-" + f["fieldname"]
        if frappe.db.exists("Custom Field", cf_name):
            skipped.append(cf_name)
            continue
        cf = frappe.new_doc("Custom Field")
        cf.dt = dt
        cf.fieldname = f["fieldname"]
        cf.fieldtype = f["fieldtype"]
        cf.label = f.get("label")
        cf.insert_after = f.get("insert_after")
        cf.read_only = f.get("read_only", 0)
        cf.no_copy = f.get("no_copy", 0)
        cf.search_index = f.get("search_index", 0)
        cf.collapsible = f.get("collapsible", 0)
        if f.get("precision"):
            cf.precision = f["precision"]
        cf.insert(ignore_permissions=True)
        created.append(cf_name)

# Fields are added in dependency order (each insert_after points at the
# previous one), so commit only after the whole set lands.
frappe.db.commit()

frappe.flags.ok = 1
frappe.flags.created = created
frappe.flags.already_present = skipped
```

If it complains about `insert_after` for `mute_sounds` (a standard User field —
present on the sibling Kaitet site, but versions differ), swap it for any field
that does exist on User, or clear it and let the field land at the end. Position
is cosmetic; nothing reads these by position.

Call it once, signed in as a System Manager. From the browser console on the
site, or curl with a session cookie:

```js
// browser console, while logged into the desk
await frappe.call({ method: "kahawa_install_fields" })
```

Expect `created` to list all 6 entries on the first run (5 fields + the section
break), and `already_present` to list them on any re-run.
**Then delete this Server Script** — it exists only to bootstrap the fields.

If you would rather not paste a temporary script, the same loop works verbatim in
a shell, where `create_custom_fields` is also available:

```bash
bench --site kaitet-group.upande.com console
```
```python
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
create_custom_fields(fields, ignore_validate=True)   # `fields` = the dict above
frappe.db.commit()
```

---

## 1. Kahawa Trail - Submit Issue

Script Type: **API** · API Method: **`kahawa_submit_issue`**

```python
# ===========================================================================
# Kahawa Trail - Submit Issue
# ---------------------------------------------------------------------------
# Endpoint: POST /api/method/kahawa_submit_issue
# Args: subject (required), description (optional)
#
# Raises an ERPNext Issue on behalf of the calling app user. Runs with
# ignore_permissions because harvest clerks have no Issue role of their own —
# the record is still stamped with their user as owner/raised_by, and the two
# companion endpoints (kahawa_support_updates / kahawa_support_reply) scope
# strictly to issues whose owner is the caller.
#
# The app appends a diagnostic JSON block to `description`; we wrap the whole
# thing in <pre> (HTML-escaped) so it stays readable in the desk form, since
# Issue.description is a Text Editor field.
# ===========================================================================

user = frappe.session.user
if not user or user == "Guest":
    frappe.throw("You must be signed in to send a report")

subject = (frappe.form_dict.get("subject") or "").strip()
if not subject:
    frappe.throw("Subject is required")

description = frappe.form_dict.get("description") or ""
safe_description = description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

issue = frappe.new_doc("Issue")
issue.subject = ("[Kahawa Trail] " + subject)[:140]
issue.description = '<pre style="white-space:pre-wrap;font-family:inherit">' + safe_description + "</pre>"
issue.raised_by = frappe.db.get_value("User", user, "email") or user
issue.status = "Open"
issue.insert(ignore_permissions=True)

# Output goes on frappe.flags, NOT frappe.response. safe_exec only exposes
# frappe.response when it is already non-empty, so writing to it is rejected
# outright in some contexts; `execute_api_server_script` returns frappe.flags
# and the handler assigns that dict to response["message"] — which is exactly
# the shape the app reads (res.message.issue).
frappe.flags.issue = issue.name
frappe.flags.subject = issue.subject
```

> The `[Kahawa Trail]` subject prefix is load-bearing — script #5 uses it to tell
> app issues apart from the site's other Issues before pushing anything.

---

## 2. Kahawa Trail - Support Updates

Script Type: **API** · API Method: **`kahawa_support_updates`**

```python
# ===========================================================================
# Kahawa Trail - Support Updates
# ---------------------------------------------------------------------------
# Endpoint: POST /api/method/kahawa_support_updates
# Args: none (scoped to frappe.session.user)
#
# Returns the caller's own support threads with every reply on them, so the app
# can (a) render the inbox and (b) notice a NEW reply and raise a local
# notification. Replies come from three places, merged into one timeline:
#   - Comment      : an agent typing in the Issue's comment box
#   - Communication: an emailed reply threaded onto the Issue
#   - resolution_details: the final answer typed into the Issue itself
#
# `last_reply_*` deliberately describes the last reply the user did NOT write,
# so the app never notifies someone about their own message.
#
# Sorting note: the merged timeline is sorted via a [timestamp, seq, payload]
# list so it needs no lambda/key function, and `seq` guarantees two replies
# sharing a timestamp never fall through to comparing dicts.
# ===========================================================================

user = frappe.session.user
if not user or user == "Guest":
    frappe.throw("You must be signed in")

MAX_THREADS = 25
MAX_REPLIES = 60

issues = frappe.get_all(
    "Issue",
    filters={"owner": user},
    fields=["name", "subject", "status", "creation", "modified", "resolution_details"],
    order_by="modified desc",
    limit_page_length=MAX_THREADS,
)

threads = []

for iss in issues:
    stamped = []
    seq = 0

    comments = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "Issue",
            "reference_name": iss.name,
            "comment_type": "Comment",
        },
        fields=["owner", "creation", "content"],
        order_by="creation asc",
        limit_page_length=MAX_REPLIES,
    )
    for c in comments:
        seq = seq + 1
        stamped.append([str(c.creation), seq, {
            "by": c.owner,
            "on": str(c.creation),
            "text": c.content or "",
            "mine": 1 if c.owner == user else 0,
        }])

    comms = frappe.get_all(
        "Communication",
        filters={"reference_doctype": "Issue", "reference_name": iss.name},
        fields=["sender", "communication_date", "content"],
        order_by="communication_date asc",
        limit_page_length=MAX_REPLIES,
    )
    for m in comms:
        seq = seq + 1
        stamped.append([str(m.communication_date), seq, {
            "by": m.sender,
            "on": str(m.communication_date),
            "text": m.content or "",
            "mine": 1 if m.sender == user else 0,
        }])

    if iss.resolution_details:
        seq = seq + 1
        stamped.append([str(iss.modified), seq, {
            "by": "Resolution",
            "on": str(iss.modified),
            "text": iss.resolution_details,
            "mine": 0,
        }])

    stamped.sort()

    replies = []
    last_reply = ""
    last_reply_on = ""
    last_reply_by = ""
    for row in stamped:
        payload = row[2]
        replies.append(payload)
        if not payload["mine"]:
            last_reply = payload["text"]
            last_reply_on = payload["on"]
            last_reply_by = payload["by"]

    threads.append({
        "issue": iss.name,
        "subject": iss.subject,
        "status": iss.status,
        "opened_on": str(iss.creation),
        "reply_count": len(replies),
        "last_reply": last_reply,
        "last_reply_on": last_reply_on,
        "last_reply_by": last_reply_by,
        "replies": replies,
    })

# See the note in Submit Issue: output goes on frappe.flags, which the handler
# assigns to response["message"] — so the app still reads res.message.threads.
frappe.flags.threads = threads
```

---

## 3. Kahawa Trail - Support Reply

Script Type: **API** · API Method: **`kahawa_support_reply`**

```python
# ===========================================================================
# Kahawa Trail - Support Reply
# ---------------------------------------------------------------------------
# Endpoint: POST /api/method/kahawa_support_reply
# Args: issue (required), message (required)
#
# Lets the reporter answer back from inside the app, so a support question
# ("which phone was it?") doesn't dead-end. Only the issue's own owner may
# post, which is what keeps the elevated insert safe.
#
# A reply on a Resolved/Closed issue reopens it — otherwise the answer lands on
# a thread nobody is watching any more.
# ===========================================================================

user = frappe.session.user
if not user or user == "Guest":
    frappe.throw("You must be signed in")

issue_name = (frappe.form_dict.get("issue") or "").strip()
message = (frappe.form_dict.get("message") or "").strip()
if not issue_name or not message:
    frappe.throw("Both issue and message are required")

owner = frappe.db.get_value("Issue", issue_name, "owner")
if not owner:
    frappe.throw("That report no longer exists")
if owner != user:
    frappe.throw("You can only reply to your own reports")

safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

comment = frappe.new_doc("Comment")
comment.comment_type = "Comment"
comment.reference_doctype = "Issue"
comment.reference_name = issue_name
comment.content = safe_message
comment.comment_email = user
comment.comment_by = user
comment.insert(ignore_permissions=True)

status = frappe.db.get_value("Issue", issue_name, "status")
if status in ("Resolved", "Closed"):
    frappe.db.set_value("Issue", issue_name, "status", "Open", update_modified=True)

frappe.flags.issue = issue_name
frappe.flags.ok = 1
```

---

## 4. Kahawa Trail - Register Push Token

Script Type: **API** · API Method: **`kahawa_register_push_token`**

Optional — only needed for server-initiated push. Without it the app logs a
failed registration silently and notifications still arrive via polling.

```python
# ===========================================================================
# Kahawa Trail - Register Push Token
# ---------------------------------------------------------------------------
# Endpoint: POST /api/method/kahawa_register_push_token
# Args: token (required, ExponentPushToken[...]), device_id (optional)
#
# Stores the caller's Expo push token on their own User record so support
# replies can be pushed to the phone. Tokens live newline-separated in
# User.custom_expo_push_tokens — one line per device, since a clerk may use
# both a shared handset and their own.
#
# Kept to a Custom Field rather than a new DocType on purpose: this is a small
# write-mostly cache that can be cleared at any time (devices re-register on
# next launch), so it does not warrant a doctype, list view or permissions of
# its own.
#
# Capped at 5 tokens per user, newest kept, so a factory-reset-happy device can
# never grow the field without bound.
# ===========================================================================

user = frappe.session.user
if not user or user == "Guest":
    frappe.throw("You must be signed in")

token = (frappe.form_dict.get("token") or "").strip()
if not token or not token.startswith("ExponentPushToken"):
    frappe.throw("A valid Expo push token is required")

MAX_TOKENS = 5

existing_raw = frappe.db.get_value("User", user, "custom_expo_push_tokens") or ""
tokens = []
for line in existing_raw.split("\n"):
    candidate = line.strip()
    if candidate and candidate != token and candidate not in tokens:
        tokens.append(candidate)

tokens.append(token)
if len(tokens) > MAX_TOKENS:
    tokens = tokens[-MAX_TOKENS:]

frappe.db.set_value("User", user, "custom_expo_push_tokens", "\n".join(tokens), update_modified=False)

frappe.flags.ok = 1
frappe.flags.tokens = len(tokens)
```

---

## 5. Kahawa Trail - Push Support Reply

Script Type: **DocType Event** · Reference Doctype: **`Comment`** · Event:
**`After Insert`**

Optional, and inert until an FCM v1 service-account key is uploaded to EAS —
until then no device has a token, so this finds none stored and exits.

```python
# ===========================================================================
# Kahawa Trail - Push Support Reply
# ---------------------------------------------------------------------------
# On: Comment / After Insert
#
# When support comments on a Kahawa Trail issue, push the reply to the phone
# that raised it. This is the server-initiated half of the notification story;
# the app also polls and raises the banner locally, and the two de-duplicate on
# the app side (it records which reply timestamp it already notified about), so
# whichever path arrives first wins and the other stays silent.
#
# SAFETY: the entire body is wrapped in try/except. This runs inside the
# commenting user's transaction — an exception here would roll back a support
# agent's reply, which is far worse than a missed notification. Push failures
# are logged and swallowed.
#
# Requires an FCM v1 service-account key in EAS for Android delivery. Until that
# exists, devices never obtain a push token, this script finds none stored, and
# it exits quietly — the app's own polling still delivers the notification.
# ===========================================================================

try:
    if doc.comment_type == "Comment" and doc.reference_doctype == "Issue" and doc.reference_name:
        issue = frappe.db.get_value(
            "Issue", doc.reference_name, ["owner", "subject"], as_dict=True
        )

        # Only Kahawa Trail threads, and never notify someone about their own
        # message. This site carries 1200+ unrelated Issues.
        is_app_issue = bool(issue) and (issue.subject or "").startswith("[Kahawa Trail]")
        is_from_someone_else = bool(issue) and issue.owner != doc.owner

        if is_app_issue and is_from_someone_else:
            raw_tokens = frappe.db.get_value("User", issue.owner, "custom_expo_push_tokens") or ""
            tokens = []
            for line in raw_tokens.split("\n"):
                candidate = line.strip()
                if candidate.startswith("ExponentPushToken") and candidate not in tokens:
                    tokens.append(candidate)

            if tokens:
                # Flatten the stored HTML down to one notification line.
                body = frappe.utils.strip_html(doc.content or "").strip()
                body = " ".join(body.split())
                if len(body) > 140:
                    body = body[:137] + "..."
                if not body:
                    body = issue.subject or "You have a new reply"

                who = (doc.comment_email or doc.owner or "Support").split("@")[0]

                messages = []
                for tok in tokens:
                    messages.append({
                        "to": tok,
                        "title": "Reply on " + doc.reference_name,
                        "body": who + ": " + body,
                        "sound": "default",
                        "channelId": "support",
                        "priority": "high",
                        "data": {"issue": doc.reference_name},
                    })

                res = requests.post(
                    "https://exp.host/--/api/v2/push/send",
                    json=messages,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=10,
                )
                if res.status_code >= 300:
                    frappe.log_error(
                        "Expo push rejected (" + str(res.status_code) + "): " + res.text[:400],
                        "Kahawa Trail push",
                    )
except Exception as e:
    # Never let a notification take down a support agent's reply.
    frappe.log_error(str(e)[:400], "Kahawa Trail push failed")
```

> `requests` is pre-loaded in Server Scripts on the Kaitet sites (the Mpesa and
> Biflorica scripts use it). If `kaitet-group.upande.com` runs a stricter
> `safe_exec` whitelist and rejects `requests`, this script is the only one that
> needs it — the other four work regardless, and push simply stays on the
> polling path.

## Verifying

After pasting, from the desk browser console:

```js
// should return {threads: [...]} — empty list is a pass
await frappe.call({ method: "kahawa_support_updates" })
```

Then from the app: ⛑ (top bar) → Report an issue → Send. You should get an
`ISS-….` number back, with the screenshot attached to the Issue.

Confirm the duplicate guard is armed:

```sql
SELECT COLUMN_NAME, COLUMN_KEY FROM information_schema.COLUMNS
 WHERE TABLE_NAME = 'tabHarvest Log' AND COLUMN_NAME = 'client_ref';
-- expect one row, COLUMN_KEY = 'MUL' (indexed)
```

## Migrating into the app

When `upande_coffee` can be deployed again, the tidy end state is:

- Custom fields: already in `upande_coffee/custom_fields.py`
  (`create_coffee_custom_fields`), so `bench migrate` creates them. The
  installer script above becomes unnecessary.
- Server Scripts: either leave them (they are site data, and Frappe can export
  them as fixtures — add `"Server Script"` to the fixtures list in `hooks.py`
  and run `bench export-fixtures`), or port them into
  `upande_coffee/api/supportapi.py` as `@frappe.whitelist()` functions. Porting
  means the app must call dotted paths instead of bare names, so it needs a
  matching change in `src/services/api.ts` and an OTA push.

Until one of those happens, these scripts live **only in the site database** —
they are not in version control anywhere except this file. Back them up before
any site restore.
