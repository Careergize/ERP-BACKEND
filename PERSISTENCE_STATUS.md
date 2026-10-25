# Supporting-record persistence — 25 October 2026

This is a partial persistence implementation, not completion of all ERP posting.
The new endpoints do not bypass existing operational authority gates.

## Implemented locally

`SharedEntry` stores supporting data and an optimistic version. `EntryRevision`
retains each accepted version, authenticated actor, server timestamp, command UUID
and SHA-256 content digest. Updates and evidence are atomic. A repeated command is
idempotent; changed retries and stale versions return 409. Evidence cannot be
updated/deleted through model/queryset methods. Production database grants/backups
remain a deployment responsibility; this is not a database-administrator tamper-proof claim.

`GET /api/shared-entries/` returns only permitted record groups and write
capabilities. `PUT /api/shared-entries/{key}/` requires a server capability and
the relevant effective role. Inactive roles/accounts cannot gain access from
frontend labels. `GET /api/management/activity/` provides cursor-paginated saved
versions for Admin, Accounts, Production Manager and Project Manager with the
history capability. The frontend exposes this through **Entry History**.

Connected supporting groups:

- QC drafts, schedules, template revisions, cleaning areas and findings.
- Costing drafts/review requests, stock-price/policy snapshots and exchange rates.
- Customer prospects, company/bank settings and reusable terms.
- Product conversions, stock thresholds and pieces edits.
- Generic Masters item/supplier/department reference entries. These do not create
  canonical Product/Supplier records for the existing purchasing API.
- Author-scoped quotation editor workspaces, correspondence, customer responses,
  costing links and new revisions. Canonical submission, approval, fulfillment and
  delivery still use the existing quotation services. Management can inspect each
  author's retained workspace versions through Entry History.

Accepted snapshots are limited to 2 MB and lists to 10,000 objects; the current
supporting API is not a replacement for normalized high-volume transaction APIs.

The frontend loads these records without blocking unrelated pages, queues updates,
waits for confirmation in connected save forms, surfaces errors/conflicts, offers
retry/export/explicit discard-and-reload, warns before closing with pending writes,
and disables sign-out while shared saves remain pending. No offline success or
local-storage substitute is used. Initial loading rejects supporting edits until
the backend is ready. Existing independent APIs remain usable.

## Still not integrated

Finished-goods movements, allocations, dispatch/reversal, production execution and
QC batch/stock-transfer tracking, invoice issuance, QC submission/review/releases,
and costing approval require dedicated validated backend services. Their older
frontend-only paths are not converted by this change. The supporting API rejects
release/approved-costing writes rather than authorizing them through generic JSON.

An attempt to replace existing operational actions with always-pending drafts was
rejected by automatic approval review because it could disable legitimate existing
workflows. That rejected patch was not applied. Application-wide transaction/audit
middleware and application-wide loading gates were also rejected and not applied.
History is explicit in the new supporting endpoints, not a global request logger.

No data previously lost from browser memory can be reconstructed. Confirmed saves
persist; a backend outage can leave an explicitly unsaved pending change requiring
retry/export. Neither deployment nor real browser save/reload verification has
been performed against the remote API configured by the frontend.

## Proposed authority mapping requiring business confirmation

This table is derived from current frontend checks, **not an approved SOP**. The
existing backend `require_operational_authority()` remains unchanged.

| Action | Proposed roles | Required separation/conditions |
| --- | --- | --- |
| Record/submit QC | Assigned QC Inspector, QC Supervisor, Admin | Complete exact-subject checklist; submitted responses freeze |
| Review/pass/fail QC | QC Supervisor, Admin | Reviewer differs from inspector; recorded remarks |
| Quality release | QC Supervisor, Admin | Independently passed inspection; unresolved findings block release |
| Final finished-stock authorization | Production Manager, Admin | Prior quality release; exact batch and bounded accepted/rejected quantities |
| Record production runs/completion | Production Operator, Production Manager, Admin | Approved job/stages, authorized quantities/units, machine/production QC holds enforced |
| Post finished-stock receipt | Store Keeper, Admin | Prior final authorization; exact product/batch/unit; idempotent posting |
| Dispatch finished goods | Store Keeper, Receptionist, Admin | Approved order; known available stock; QC hold and quantity checks |
| Non-sales stock OUT | Store Keeper, Admin | Explicit purpose/reference/reason; adjustments require Admin |
| Issue invoice | Accounts, Receptionist, Admin | Confirmed saved dispatches; no over-invoicing; financial snapshot verified on server |

Confirm or correct these roles and their approval evidence before implementing
activation of the remaining operational paths. Persisting a supporting draft
does not constitute production approval, stock posting, customer delivery or invoicing.

## Verification

- 29 isolated SQLite tests passed: 14 shared-entry tests, operational authority
  regression and 14 existing quotation tests. These are not PostgreSQL concurrency tests.
- Django system checks and migration drift checks passed.
- Frontend TypeScript/build, shared-entry transport checks, quotation helpers and
  built Tailwind CSS checks passed. Existing large-bundle warning remains.
- A broader run including production workflow tests had 14 failures, including
  conflicts with the current authority gates; it is not an all-green regression claim.
- Local migrations `erp.0017` and `erp.0018` applied successfully. Local read-only
  checks returned 200 for both shared records and management history using an
  existing active admin account. No sample business entries were added to the real DB.
