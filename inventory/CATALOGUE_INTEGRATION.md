# Consumables and Spares backend integration — 28 October 2026

## Scope and current status

Implemented on `codex/catalogue-integration` in both local repositories. No merge,
commit, push, deployment, shared/production migration, database reset, role grant,
accounting posting or automatic data transfer was performed. Existing uncommitted
work, including staged catalogue tests, was preserved.

This report supersedes earlier frontend-only status for the routed Consumables
workspace. Legacy demonstration helpers/data remain separate in source for
compatibility and review; they are not loaded into the production API workspace.

Verified in isolation: catalogue API contracts, generated codes, persistence/reload,
IN/OUT balances, duplicate submission handling, permission checks, module separation,
audit immutability at the API/ORM boundaries, additive legacy-data migration and
file-backed SQLite concurrency. Frontend transport/SSR checks and production build
pass. Browser interaction remains unverified; see limitations below. These are not
claims of deployment, database-level tamper-proofing or accounting readiness.

## Feature classification

- Working and tested in isolation: master CRUD/legacy compatibility, permanent
  Consumables codes, code immutability, request replay, independent stock ledgers,
  IN/OUT/openings, negative/backdated-stock validation, histories, summaries,
  role/model-permission enforcement, server audit evidence and legacy-data migration.
- Implemented but not fully verified: the connected production frontend and Spares
  posting forms (transport/SSR/build tested; interactive browser/mobile acceptance
  remains unverified), and PostgreSQL runtime/concurrency.
- Pending: approved target migration, permission assignments, deployment and live
  acceptance; currency/valuation policy and any supplier-master linkage. Accounting
  postings/approval/adjustment workflows are not introduced by this change.
- Blocked verification: Windows Computer Use could not establish the current browser
  URL confidently enough to enforce policy and stopped before interaction. No browser
  pass or audit/accounting-readiness certification is asserted.

## Discovery and gap analysis (completed before approval)

Both `/api/consumables/` and `/api/spares/` already supported basic paginated master
list/create/retrieve/update and capability reads. Spares also had stock/history
reads and an internal Decimal/idempotent movement service, but POST movements was
intentionally denied pending confirmed authority. Consumables had no movement model,
stock endpoints, categories, costs, thresholds or durable enhanced workflow.

The frontend's Saved Register and dedicated Spares used those APIs. Consumables'
enhanced views used local React state, provisional codes, sample balances and local
activity. Both modules shared ItemRegisterPage and registerApi; the general Axios
client, authentication, routes and sidebar were not changed for integration.

Existing audit foundations were found in erp.SharedEntry, EntryRevision and
ImmutableEvidence. RawMaterial/RawMovement and finished-goods ledgers were separate
existing infrastructure and were not repurposed. Accounting already posts selected
raw/finished/invoice events; no catalogue-specific posting requirement was found.

Read-only inspection identified the configured local database as
`F:\WORK\ERP\backend\db.sqlite3` (SQLite). At inspection its catalogue tables had
zero Consumables, zero Spares and zero spare movements. Inventory 0010 was applied;
accounting migrations were not recorded as applied. No catalogue grants were
assigned to roles or users in that local file. These observations do not establish
remote/production data or permissions. Do not run an unrestricted live `migrate`
command as part of this change: unrelated migrations may also be pending.

## Models and field mapping

Existing UUID primary keys, code/name/description/unit/status/creator/timestamps and
all old code/unit labels remain. Additive inventory migration
`0011_catalogue_integration` introduces:

- Catalogue master metadata: updated_by, supplier/department/location labels,
  expense classification, optional unit cost and creation-request ID/digest.
- Consumables: category/subcategory, minimum stock, reorder quantity and optional
  machine. Blank legacy classifications remain blank; they are not guessed.
- A separate ConsumableStockMovement table. SpareStockMovement is retained and
  receives additive metadata, business date, payload digest and a generated
  transaction reference. Old references/IDs/actors/timestamps remain unchanged.
- CatalogueSequence for persistent allocation and write serialization, and
  ConsumableCodeReservation for durable non-reuse of Consumables codes.

Opening Stock is an explicit opening movement, not a second editable balance.
Stock in Hand and IN/OUT totals derive from that module's ledger. Unknown openings
stay null; explicit zero is valid. Last Purchase Date derives from recorded IN
business dates; undated legacy receipts remain unknown rather than being assigned
a guessed purchase date. Units are extended only for these catalogue models while preserving
Each/Both/Litres/Kilos. Labels such as KG/Kilos or Litre/Litres are not automatically
converted or merged. Supplier/department/expense mappings remain validated labels
and transaction snapshots; no duplicate supplier/department/ledger master is added.

## Permanent codes, references and concurrency

New classified Consumables creation omits `code`. The backend validates the seven
categories and subcategories, allocates CON-{prefix}-{sequence}, persists it and
returns the final code. Category changes never regenerate the saved code.

The allocator uses persisted counters under a catalogue-only write lock and code
reservations with database uniqueness. It does not use browser storage or MAX(code).
The migration reserves existing codes, case-normalized for collision checks, without
editing their original display values or assigning historical audit metadata.
Committed codes are not reused for inactive/removed records. Failed atomic writes
roll back unpublished allocations. Existing non-CON manual-code creation clients
remain compatible; a caller cannot override the new CON- namespace. Item codes are
immutable through API updates, model save and queryset code-update shortcuts.
Spares keep their existing caller-supplied code format, with saved identities locked.

New transactions have generated CIN-/COUT- and SIN-/SOUT- year/sequence references
in `transaction_reference`. Existing `reference` remains a source reference, so old
Spares callers/history are not overwritten. Request IDs bind actor, item and payload;
identical retries replay the original result, and changed-payload reuse is rejected.
Master creation also supports a stable creation_request_id. Frontend forms retain
request IDs across failures, disable duplicate in-flight submission and acknowledge
success only after the server responds.

Every catalogue mutation begins with a database write to a seeded catalogue lock
row before reading counters/balances. This acquires SQLite's writer lock and a
PostgreSQL row lock without changing global connection/transaction settings.
SQLite lock/busy conditions have bounded retries and a safe 503 response. Catalogue
writes serialize with each other; unrelated inventory services are not redirected.
The strategy was exercised using independent connections and a disposable file-backed
SQLite database. PostgreSQL runtime/concurrency is not verified.

## Transactions and authorization

The approved unconditional Spares posting denial is replaced by existing model-
permission checks. No user/role assignments or authentication logic are changed.

- Master reads: inventory.view_consumable / view_spareitem.
- Master creates/edits: corresponding add/change permissions.
- Posting/opening: item-view plus add_consumablestockmovement /
  add_sparestockmovement.
- Movement and module audit reads: item-view plus the corresponding movement-view
  permission. Existing UI role visibility is unchanged and is not authorization.

Ordinary quantities must be positive, finite and within six decimal places; units
must match the exact active item. The first opening is explicit and unique. Unit
changes lock after posting. OUT cannot exceed stock, including chronological checks
for backdated issues. Business dates cannot be in the future or before the opening.
Consumables receipts/issues validate the implemented department, supplier/location,
recipient and purpose fields. Authenticated actors and server timestamps cannot be
supplied by form fields. All writes, allocations and audit evidence commit/rollback
together. There are no adjustment, approval, purchasing or financial commands added.

## Audit reuse and protection

Catalogue events append to the existing SharedEntry/EntryRevision infrastructure
under internal `catalogue-{module}-{uuid}` keys. The generic shared-entry write
allowlist is unchanged and cannot write these keys. Ledger state remains in dedicated
catalogue tables; audit JSON is never an alternate stock-posting API.

Events include stable item ID/code, create/update/status or IN/OUT action, previous/
new values, actor ID, server timestamp, transaction reference, department and remarks.
Stock events include balance/cost before and after. Existing movement models reuse
ImmutableEvidence's insert-only behavior. No mutation/delete audit endpoints exist,
and ORM update/delete attempts are tested. Authorized management history can show
these additional catalogue events using its existing generic viewer; no management
API/UI code was modified. DBA access/grants and infrastructure tamper resistance
remain deployment controls; this is not a tamper-proof audit certification.

## Endpoints

Reused paths:
- GET/POST /api/{consumables|spares}/
- GET/PUT/PATCH /api/{module}/{uuid}/
- GET /api/{module}/capabilities/
- GET /api/spares/{uuid}/stock/
- GET/POST /api/spares/{uuid}/movements/ (POST now permission-controlled)

Added only under the existing catalogue viewsets:
- GET /api/consumables/{uuid}/stock/
- GET/POST /api/consumables/{uuid}/movements/
- GET /api/{module}/history/ — paginated cross-item movement history
- GET /api/{module}/activity/ — paginated module audit history
- GET /api/{module}/summary/ — all-matching-item overview/report metrics

List/summary/history use existing search, unit/status and category conventions,
with additive department, office, low_stock and name/code/stock ordering filters. Historical
department filters match transaction/event department rather than only the item's
home department. Summary totals cover all matching items, not just the current page.
Existing paginated count/next/previous/results response conventions remain.
Capabilities add catalogue_version=1, can_open and can_view_movements. The frontend
reports an undeployed enhanced API instead of substituting sample success.

## Frontend integration

The routed Consumables page now opens ConnectedConsumables. Saved Register is
retained and enhanced master forms reuse existing Card, input, button and modal
conventions. Overview/Master/IN/OUT/Office/History/Alerts/Reports/Activity load API
records with explicit loading, empty, retry and validation states. Codes are read-only
and assigned on save. Legacy unclassified records remain unclassified when edited.
API errors never substitute demonstration records. Server error bodies are not echoed
as raw HTML/internal tracebacks.

Spares keeps ItemRegisterPage, its route/sidebar, pagination, filters and per-item
history. Its existing IN/OUT controls now open the reusable catalogue movement form,
subject to capabilities. Optional expense/cost/location metadata and audit identity
are available without replacing its master editor or code format. Dropdowns currently
select items on the loaded register page; use search/page navigation to locate an
item first. Broader asynchronous item lookup is not claimed.

The two earlier demo records DEMO-2 (Imported bearing) and DEMO-3 (Local drive belt)
remain preserved in the legacy demo dataset/read-only review component. Nothing
imports, transfers or reclassifies them into real inventories. Live legacy migration
requires a separate reviewed data plan and approval.

## Accounting boundary and remaining deployment work

Expense / Ledger Classification is configurable metadata only. No accounting API,
journal, VAT, LED, COP, invoice, costing, financial report, raw or finished-goods
posting was changed. Stock display value uses latest recorded unit cost; missing
openings/costs are explicit and excluded. Currency is unspecified and no FX or
financial valuation correctness is asserted.

Pending: review/apply the inventory migration to an explicitly approved target only,
verify required existing ERP audit tables are already migrated, assign approved
catalogue permissions through existing administration, confirm currency/valuation
policy and supplier-master linkage if needed, verify PostgreSQL if that is the real
deployment engine, and perform browser/mobile/live environment acceptance. Deploy
backend/schema/frontend together. Do not point the new backend code at an unmigrated
shared catalogue database. No automatic merge or deploy is included.

## Tests and evidence

Safety runner: `python -B test_baseline_runner.py ...` skips dotenv, uses only in-memory
databases, blocks outbound sockets, and never migrates the configured local file.
The separate concurrency script uses/removes a disposable file below backend/tmp.

- Baseline: 236 backend tests, 230 passed, six existing errors, no assertion failures.
  All six errors are WorkflowBaselineTests calling a missing prepare_quote fixture.
  Evidence: tmp/catalogue-baseline.txt.
- Focused catalogue/API/JWT/migration tests: 21 passed.
  Evidence: tmp/catalogue-integration-tests.txt.
- Catalogue plus existing ERP/inventory migration regression subset: 24 passed.
  Evidence: tmp/catalogue-migration-regressions.txt. An initial cross-app dependency
  broke two older rollback/upgrade tests; removing that unnecessary schema dependency
  fixed both without changing their test code or shared migrations.
- File-backed concurrency: passed eight simultaneous distinct-code creations,
  duplicate master/movement replays, two competing issues with only one accepted,
  reference uniqueness and database reload. Evidence: tmp/catalogue-concurrency.txt.
- Django system check: passed. Migration drift: no changes detected. Graph consistency:
  passed. Evidence: tmp/catalogue-drift.txt and tmp/catalogue-graph.txt.
- All 25 frontend check scripts passed, including the new catalogue transport/SSR
  checks and existing auth, routes/sidebar/register, accounts, production, order/job,
  inventory, quotation, invoice, QC and integration checks. TypeScript and production
  build passed. No lint script/configuration is available; the existing large-bundle
  warning remains.
- Browser QA: Computer Use stopped before interaction because it could not determine
  the current Windows browser URL confidently enough to enforce policy. No browser
  click-through pass is claimed. Its isolated fixture servers/harness were stopped
  and removed; no shared data was used.

Final full backend suite: 249 tests, 243 passed, the same six pre-existing errors,
no assertion failures. Error-name sets were compared programmatically with the
baseline and match exactly. Evidence: tmp/catalogue-regression-final.txt. No unrelated
fixtures were rewritten to conceal those errors. Name/code/stock sorting and final
catalogue/migration assertions, including undated legacy purchase-date preservation,
were also checked in the focused regression subset after the full run.

A final read-only inspection of the shared local database still found zero catalogue
records, no new catalogue tables and inventory migrations only through 0010. The
implementation has not applied 0011 or any unrelated accounting migration there.

## Exact source/document files in this integration

Backend modified: inventory/catalog_models.py, inventory/catalog_api.py,
inventory/catalogue_service.py (new), inventory/models.py, inventory/spare_service.py,
inventory/migrations/0011_catalogue_integration.py (new), inventory/test_catalog.py
(approved posting/stock contract assertions; pre-existing staged JWT work preserved),
inventory/test_catalogue_integration.py (new), catalogue_concurrency_check.py (new),
and inventory/CATALOGUE_INTEGRATION.md (new).

Frontend modified: src/lib/registerApi.ts, src/pages/Registers/ItemRegisterPage.tsx,
src/pages/Consumables/ConsumablesPage.tsx, scripts/check-consumables-demo.mjs
(saved Spares code now correctly read-only).
Frontend added: src/lib/catalogueApi.ts,
src/pages/Consumables/CatalogueForms.tsx,
src/pages/Consumables/ConnectedConsumables.tsx,
scripts/check-catalogue-integration.mjs.
Documentation updated: docs/consumables-frontend.md, DEVELOPMENT_STATUS.md,
BACKEND_TODO.md, DATA_MODELS.md, BUSINESS_RULES.md and CHANGELOG.md.

New evidence files (relative to backend/) are separate from source:
- tmp/catalogue-baseline.txt
- tmp/catalogue-concurrency.txt
- tmp/catalogue-drift.txt
- tmp/catalogue-focused.txt
- tmp/catalogue-graph.txt
- tmp/catalogue-integration-tests.txt
- tmp/catalogue-migration-regressions.txt
- tmp/catalogue-regression.txt (superseded intermediate run)
- tmp/catalogue-regression-final.txt

Existing tmp/register-404-regressions.txt and tmp/server-verification-regressions.txt
were present before this integration and were not replaced.
