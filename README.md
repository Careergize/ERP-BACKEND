# Navapack ERP backend

## Shared direct Sales Order production workflow

`/api/production-workflow/` provides processes/, models/, orders/, jobs/ and
orders/{id}/allocate/ plus jobs/{id}/{edit|approve|issue}/. Models/processes and direct
Sales Orders are stored separately from the earlier prototype ERP endpoints. Run
migrations 0011/0012 before use. Receptionist/Admin may create direct orders; PM/Admin
corrects/approves jobs; Store/Admin issues approved requirements. Other staff may read
shared definitions/order/job queues according to seeded permissions.

A manufacturing line selects a saved model revision, recipe, explicit production output
per batch and required date. Its model's selected ordered processes become job stages.
Materials scale by order quantity/output per recipe batch, with exact output units;
percentage recipes use explicit mass units and sum to 100. Stock-only lines are excluded
from Job Cards; mixed orders allocate one parent with only manufacturing items. Deferred
allocation remains possible and idempotent. No separate costing estimate is required.

Order creation uses creationKey UUID; changed retries fail. Job actions use expectedVersion
and authenticated server identity. Approval forwards the parent to Store Issue Pending.
Store issue locks the job/raw rows, bounds quantities against remaining requirements
and current known physical balance, and saves RawMovement OUT/status/audit atomically.
A stale repeated issue fails before writing another movement. Manual procurement stock
is unaffected. PM edits cannot change ordered quantity/unit or issued/approved scope.

Legacy examples/quotation conversion and later production/QC/dispatch retain their existing
frontend lifecycle. Server-owned QC hold/release persistence, order amendments/reversals
and PostgreSQL concurrency remain pending; this scope does not certify those workflows.
Django checks/migration drift, 80 backend tests and relevant frontend build/checks pass
on local SQLite. Browser interaction and PostgreSQL concurrency were not exercised.


## Goods Received Notes (GRN)

`GET/POST /api/goods-received-notes/` and `GET /api/goods-received-notes/{id}/` provide
shared immutable receiving documents. Create a PO with `receipt_method: grn` (the API
default); select that PO when creating its GRN. An explicit `immediate` receipt option
remains for purchases already in hand. Immediate PO lines cannot post a GRN again.

GRN POST accepts request_id (UUID), purchase_order, receipt_date, authorized_person
(optional declared name), notes and lines: purchase_line, quantity_received,
damaged_quantity, missing_quantity, remarks. Number, supplier snapshot and receiving
user/name are server controlled. Received includes damage; only received - damaged
adds usable finished-goods Stock. Missing quantities add no stock and remain available
for a later receipt. Across all GRNs, delivered quantities including damaged goods
cannot exceed the PO quantity. Each note records previously received quantities.

All lines must belong to the PO and have linked finished-stock items. Receipt date
must be on/after the PO date and stock opening and no later than today. Save locks the
PO and commits the document/receipts/status together. Identical UUID retries return
the saved note; changed retries fail with 409. No edit/delete API is provided. Existing
immediate receipts and earlier unlinked drafts are preserved without backfilling.

Run `manage.py migrate` for 0009/0010. Existing purchasing roles receive GRN view/add
permissions; display roles cannot bypass server permissions. The frontend provides
list/search, entry, printable detail and links from Stock/PO. The authorized-person
name is a document field, not a formal approval workflow. Raw Material, manual
procurement, payments and production/QC workflows are unchanged.

Verification: all 74 backend tests and checks pass on local SQLite. PostgreSQL
concurrency has not been exercised.

## Purchase orders

`GET/POST /api/purchase-orders/` and `GET /api/purchase-orders/{id}/` provide persisted
supplier purchases. Entries include supplier/contact/address/reference, purchase and
expected delivery dates, delivery address, currency, payment terms, notes and up to
100 item rows (description, exact unit, positive quantity and nonnegative unit price).
Totals use decimal arithmetic before tax/freight. Number, receiving status and creator are
server controlled. UUID request IDs make identical retries safe; changed retries return
409. Purchases cannot be edited/deleted through this API. Every new line must select
`stock_mode: existing` with a `stock_item` ID, or `stock_mode: manual` with `new_item`
category and optional subgroup/customer/size/brand/packing_size. Existing units must
match exactly. New manual variants start at zero opening stock on the purchase date.
An immediate purchase line is its Stock IN receipt; GRN POs wait for accepted receipt
lines. Stock entries commit atomically. Retrying a save cannot create a second receipt. Duplicate manual
variants require explicit From stock selection. Immediate receipt dates after today
or before the selected variant opening are rejected. Raw Material, procurement forecasts, QC
production approvals and payments are unchanged. Supplier details are snapshots
entered on the order, independent of the supplier master.

Run `manage.py migrate`. Migrations 0005/0006 grant view/add purchase order
permissions to existing Admin, Store Keeper, Project Manager, Production Manager and
Accounts role records. Server model permissions remain authoritative. The frontend's
Purchase Orders menu supports list/search, entry and detail views; demo accounts must
sign in with a real Django account to use persistence.

Migrations 0007/0008 add the finished-stock catalog and preserve all 368 source variant
IDs, source notes, exact units and nullable opening balances from the existing frontend
reference. `GET /api/finished-stock/` returns the catalog and durable immediate/GRN receipts.
The frontend combines these receipts with its existing reference movements; other
finished-goods manual/production/dispatch workflows remain frontend session workflows.
Unknown source openings stay unknown even after a known purchase receipt. New manual
items have known zero opening and show stock once received immediately or through GRN.
Earlier draft POs remain unposted: migration never guesses that they were received.

Raw-material receipts, consumption and procurement plans now persist in the shared
database. See [inventory setup, endpoints and verification](inventory/README.md).
Run migrations before using the connected Raw Material and Procurement screens.

Quotation PDF generation, structured data extraction and selected-channel email/WhatsApp delivery are described in [quotations/README.md](quotations/README.md). Install the updated requirements and run migrations before using them.

Django 5.2 LTS and Django REST Framework starter for the adjacent React frontend. Includes JWT authentication, custom users, Django admin, customer/supplier/product masters, nested draft sales orders and draft parent job cards. SQLite is the development default; PostgreSQL is configurable.

## Start on Windows

Requires Python 3.12 or newer supported by Django 5.2. Run from `Backend`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Generate a secret and paste it into DJANGO_SECRET_KEY in .env:
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

The local workspace already has a virtual environment, generated development `.env`, and migrated development database. No default user/password is created. Create your own superuser with the command above.

Admin: http://localhost:8000/admin/ . Health: http://localhost:8000/api/health/ . API root: http://localhost:8000/api/ .

## API

| Path under `/api/` | Methods | Purpose |
| --- | --- | --- |
| `health/` | GET | Public database connectivity probe |
| `auth/login/` | POST | Username/password → access, refresh, user |
| `auth/refresh/` | POST | Refresh token → rotated token pair |
| `auth/logout/` | POST | Blacklist supplied refresh token |
| `auth/me/` | GET | Current user |
| `customers/`, `suppliers/`, `products/` | GET, POST | List/create masters |
| `customers/{id}/`, `suppliers/{id}/`, `products/{id}/` | GET, PUT, PATCH | Read/update masters; deactivate instead of delete |
| `sales-orders/`, `job-cards/` | GET, POST | List/create draft records |
| `sales-orders/{id}/`, `job-cards/{id}/` | GET | Read draft record |

Send `Authorization: Bearer <access>` on authenticated requests. Lists use `{count, next, previous, results}` pagination. Assign `view`, `add`, and `change` model permissions through admin groups for non-superusers. User `role` describes the frontend role; it grants no API permissions by itself. Provisioning users requires admin access. Login accepts username, which may be an email address if provisioned as the username. Logout revokes the supplied refresh token; an existing access token expires after 15 minutes. Run `manage.py flushexpiredtokens` periodically to clean token records.

Example sales-order body:

```json
{
  "customer": 1,
  "order_date": "2026-10-15",
  "delivery_date": "2026-10-30",
  "notes": "Customer reference",
  "lines": [{"product": 1, "quantity": "100.000", "unit_price": "2.50"}]
}
```

Orders need active customers and finished-goods products. Numbers and draft status are server-controlled. Nested line creation is atomic, quantities must be positive and prices nonnegative. `total` is a decimal gross line sum before tax/discount, not a finalized invoice amount. Job cards accept `sales_order` or null for production for stock; a database one-to-one relation enforces one parent per order. Order creation has no stock side effects.

## Frontend connection

In `../ERP-Navapack-frontend/.env`, set `VITE_API_BASE_URL=http://localhost:8000/api` and `VITE_ENABLE_MOCK_LOGIN=false`, then restart Vite. Login follows the current frontend contract. Other screens still use frontend data/fallbacks and require explicit integration; creating this backend does not connect those screens automatically.

## Verification and deployment

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

For deployment, supply a private secret, set `DJANGO_DEBUG=false`, configure allowed hosts/CORS and PostgreSQL credentials, migrate, collect static files, and serve through a production WSGI/ASGI server behind HTTPS. Secure cookies, SSL redirect and HSTS are enabled when debug is false. Configure proxy trust only for your known proxy. Login throttles use Django's local cache by default; multi-worker deployments need a shared cache and edge rate limiting. Run `manage.py check --deploy` using production settings. SQLite is for local development; PostgreSQL behavior is not yet integration-tested.

## Remaining ERP implementation

This is a backend foundation, not the complete ERP. Stock ledgers/posting, revisions and amendments, routing, material issue, approvals, QC, recycling, costing, quotations, invoicing, dashboard/trace reports and audit/idempotency/concurrency services remain to be implemented against the frontend business documentation. Draft orders/job cards deliberately expose no update, delete or approval endpoints. Product identities/units and master edits need further immutability rules before inventory integration. Admin is for trusted operators and bypasses API service validation; production workflows must use dedicated services.
