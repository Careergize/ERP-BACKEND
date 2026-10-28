# Sales backend implementation

## Scope and architecture

Implemented on codex/sales-backend in both repositories. Backend base main 3097135; frontend base codex/spares-opening-approval a8c97a0 (the existing committed Sales tabs). No push, merge, deployment, shared migration, permission grant or operational database reset was performed. The initial audit/matrix is [SALES_BACKEND_AUDIT.md](../../Gap-Analysis/SALES_BACKEND_AUDIT.md).

Existing Sales invoices are immutable snapshots in ERP SharedEntry invoices, validated by operational_records.validate_invoices against WorkflowOrder lines and confirmed dispatches. Existing accounting Document/DocumentLine are posted financial projections, not a replacement source register. No duplicate Invoice model or customer/name matching was created. Original invoice IDs and line-index identities remain traceable to the existing source. Canonical Customer FKs resolve through the existing WorkflowOrder.customerId (including the existing db: prefix); legacy records without valid IDs are excluded with explicit review issues, never guessed from buyer names or migrated automatically.

Existing JWT, RolePermissionBackend, SharedEntry/EntryRevision immutable audit storage, version/CAS transaction patterns, Decimal money helpers and ReportLab dependency were reused. Existing QC operational authority is rechecked with authorize(qc-record). No stock or financial business module was rewritten.

## Persistence, authorization and audit

Additive ERP migration 0021_sales_operations follows ERP 0020 and the swappable user migration. New models: SalesReturn, SalesCreditNote and immutable SalesReturnInspection. Return/credit rows reference the existing source entry and customer, retain source invoice/line snapshots, quantities, money components, statuses, versions, contributor IDs, server actors and timestamps. Credits link approved returns. One active credit per return is enforced by a conditional database unique constraint. Full credit of a partial return is supported; arbitrary split credits are not authorized and are explicitly unavailable. Issued documents cannot be edited/deleted; corrections/reversals require future authorized services, not overwrites.

New explicit erp permissions on SalesReturn: view_sales, edit_sales, review_sales, inspect_sales, issue_sales, view_all_sales. Existing authentication/role grant infrastructure resolves them; no role assignments are made by the migration. Real return creation also requires existing invoice-read authority. Requesters see their own return/credit objects; explicitly authorized reporting/review/QC readers have broader scope. Missing view permission yields 403, inaccessible objects yield 404. Stable customer IDs are used for filtering; no customer-tenant login/assignment system was invented. Report-all authority exposes all ERP customers deliberately; configure it only for authorized staff.

Creators, editors and submitters are contributors. None may review/approve/reject their own document, including superusers. Issuance likewise excludes contributors. Client-supplied totals, reference numbers, users, approvals or stock fields are rejected. Server recalculates all source allocations. Commands require UUID commandId; updates/transitions require expectedVersion. Existing immutable EntryRevision provides actor/payload/action-bound command replay and previous/next audit evidence. Every successful mutation is audited in the same transaction. Audit failure rolls back document/inspection changes. Internal sales audit keys are absent from generic shared-entry write scopes.

The existing invoices SharedEntry is the common allocation lock: a no-op version write is the first SQL operation in each atomic command (SQLite writer lock, PostgreSQL row lock), with bounded SQLite busy retries. Invoice content/version/history are unchanged. Submitted, Pending Approval and Approved returns reserve sold quantity; drafts do not reserve it. Submit revalidates the entire pending/approved allocation. Cancellation/rejection releases reservations. Approval recalculates cents against previously approved quantities, preventing rounding over-credit after a pending reservation is cancelled. No MAX+1 allocation is used; permanent UUID-based SR-/CN- references are unique and immutable.

## Read models and endpoints

Repository /api prefix is retained, not the suggested /api/v1 prefix. New endpoints:

- GET /api/sales/bootstrap/ — authorized source projections, scoped documents, action capabilities, reporting rows and explicit integration limitations.
- GET /api/sales/ledger/ and /api/sales/ledger/customers/{customer_id}/ — chronological customer/currency sales balances, source document references, returns and credits; paginated at 50.
- GET /api/sales/dashboard/ — per-currency gross, invoice/customer counts, approved return value/count, issued credit value, net, monthly and customer groups.
- GET/POST /api/sales/returns/ and /api/sales/credit-notes/.
- GET/PATCH /api/sales/returns/{uuid}/ and /api/sales/credit-notes/{uuid}/ — draft-only financial input editing.
- POST /api/sales/{returns|credit-notes}/{uuid}/transition/ plus submit/pending/approve/reject/cancel action aliases; issue only succeeds for eligible credits.
- POST /api/sales/returns/{uuid}/inspect/.
- GET /api/sales/credit-notes/{uuid}/preview/ — identified DRAFT PDF.
- GET /api/sales/credit-notes/{uuid}/pdf/ — issued-only official operational PDF.

Ledger/dashboard filters: from/to, customer_id, product_id, transaction_type, invoice_status, currency, search; ordering supports date, reference, customerId, debit, credit; page controls pagination. Return/credit lists support status/customer_id/invoice_id and pagination. Monetary calculations use Decimal and API money values serialize as decimal strings. Original source snapshots without an issuance-status field are explicitly Recorded, not assumed paid; explicit Draft/Cancelled invoices carry zero financial effect and cannot be returned.

Only issued credit notes reduce operational net sales. Return rows are visible but add no second financial credit. Draft/Submitted/Pending/Approved-not-issued credit documents have no posted sales reduction. Currency totals are separate; no conversion is guessed. Customer sales balances exclude cash payments/openings and are not receivables. Source-invalid issues are visible, so incomplete totals are not passed off as complete.

## QC, stock, tax and accounting limits

Submitted returns support persistent Pass/Fail/Hold inspections, inspected/accepted quantities, findings, inspector/time and immutable reinspection history. Existing QC checklist references can be linked only if they identify the exact return and their existing qc_result agrees. The existing generic QC subject/release workflow is not automatically extended to manufacture a customer-return stock authorization. Return approval requires a recorded inspection; a failed/damaged return may still need a financial credit, so a QC Pass is not silently equated to credit approval.

BLOCKED: accepted stock posting/restock/reject/scrap commands. The existing Finished Goods service only implements purchase/manufactured/manual receipt authorities; it has no approved customer-return receipt source. No stock IN, fake movement, direct balance edit, dispatch reversal or COGS reversal was introduced. Accepted quantities remain inspection evidence only. Tests assert raw/finished/accounting data is unchanged.

Money allocations use original immutable invoice net/VAT/LED snapshots and existing ROUND_HALF_UP money helper, not frontend totals or newly invented tax rates. BLOCKED: issuance of any VAT/LED-bearing credit note until confirmed jurisdictional credit-adjustment rules exist. Such credits may be drafted/reviewed but their issue action is unavailable. Zero-tax/zero-LED credits with company identity/address may be independently issued as operational credit notes. All PDF content is escaped and generated through the existing ReportLab dependency, without a new package. Draft previews are visibly identified. No fiscal certification claim is made.

BLOCKED: general-ledger credit posting and receivable adjustment. Accounting is implemented and tested, but its current command service has invoice/payment/whole reversal support, not a configured credit-note/partial-tax reversal command or approved sales-return contra mapping. No account code/mapping is guessed; every credit exposes glStatus Not posted. Issuance is not a cash refund. No placeholder journals or financial posting queue success is fabricated.

## Frontend integration

The single Sales sidebar item and four internal tabs/deep links remain unchanged. Saved mode now loads the verified Sales bootstrap and posts real create/edit/submit/review/inspect/issue commands through the existing API client. State refresh occurs after backend confirmation. Backend action capabilities/versions control real document actions. Explicit example mode remains separate and uses its original preview rules; API failures never substitute examples. Non-reporting users see permission-denied text instead of fake zero dashboards while their authorized documents remain accessible.

Server reporting rows supply actual real-record net/credit balances; returns and credits are not double-counted. Source fields and tax amounts remain read-only. Requests retain command IDs on ambiguous errors, block unrelated new writes and offer Retry unchanged command. Native form handlers await confirmation before closing. Backend PDF download checks MIME/header bytes; issued versus DRAFT routes are distinct. Browser print remains available. Attachment uploads remain unsupported; the form labels metadata-only behavior.

## Exact source files

Backend modified: config/urls.py (one include), erp/models.py (model imports).
Backend created: erp/sales_models.py; erp/sales_service.py; erp/sales_api.py; erp/sales_urls.py; erp/sales_pdf.py; erp/migrations/0021_sales_operations.py; erp/test_sales.py; sales_concurrency_check.py; this report.

Frontend created: src/lib/salesApi.ts; scripts/check-sales-api.mjs.
Frontend modified: src/context/SalesManagementContext.tsx; src/lib/salesManagement.ts; src/pages/Sales/SalesDocuments.tsx; src/pages/Sales/SalesWorkspace.tsx; src/pages/Sales/CreditNotePrint.tsx; src/pages/Sales/SalesReturnInspection.tsx; scripts/check-sales-management.mjs; affected Sales documentation/status/TODO/model/rule/changelog files. No existing Sales Orders, invoice create path, Quotations, Dispatch, Stock, auth client, sidebar or tab route was rewritten.

## Deployment and remaining acceptance

No operational migration or permission assignment was performed. Before any rollout: review migration/target DB, review grants through existing role management, confirm tax adjustment and credit-account mappings, approve a return-specific stock posting policy/service, verify exact backend/frontend releases/API origin, then browser-test authenticated multi-user flows. Shared SQLite and production settings are not used by test commands. PostgreSQL/production-scale performance, browser/native-print/mobile interaction and actual cross-process deployment behavior remain unverified. Bootstrap currently loads the authorized reporting set to preserve existing tab filtering; dedicated paginated read APIs exist but large-scale query/performance acceptance remains pending.

Full production completion is BLOCKED on the explicitly listed stock, tax and GL policies/infrastructure. This implementation must not be labelled fully posted accounting or production-ready fiscal issuance.


## Executed validation and final status

Working and tested in isolated storage:
- Focused Sales suite: 12/12 passed, including additive migration preservation. Actual JWT API calls cover source-to-ledger linkage, stable customer filtering, object visibility, drafts, validation/excess quantities, pending reservation/cancellation, QC/approval ordering, Admin/editor independence, duplicate credits, zero-tax issuance and actual %PDF bytes, taxed issuance rejection, retries/version conflicts, audit rollback and unchanged source/stock/GL records.
- File-backed SQLite race checker passed with real JWT/separate connections: competing pending reservations, same-command approval replay, duplicate credit creation and concurrent issuance produce single receipts/documents and exact net sales.
- Full isolated backend suite: 280 tests, 274 passed, six pre-existing errors, zero new assertion failures. Baseline before changes: 268 tests, six identical errors. Errors remain the existing WorkflowBaselineTests missing prepare_quote (wrong-unit issue, legacy order/job read, multi-material rollback, quotation lineage, multi-line snapshot parent, unsafe draft/same-corrector characterization). No unrelated quotation/production implementation or tests were edited.
- Initial intermediate broad runs hit 8 and then 5 extra failures when later tests inherited shared DRF throttle-cache counters (429s and a consequent owner-scope fixture failure). The new Sales tests now clear their disposable test cache in setup/cleanup. Production throttle settings and unrelated tests were untouched. The corrected full run has only the six baseline errors.
- All 28 frontend check scripts passed, including the new intercepted API adapter check. Standalone tsc -b and final npm run build passed; focused Sales/UI/API checks passed after the last reporting-display changes. Existing large-bundle and SSR useLayoutEffect warnings remain. No lint configuration exists.
- Django system check, migration graph and model drift checks passed. ERP 0021 was forward/reverse tested only on isolated databases. Git whitespace checks passed.

Evidence: backend/tmp/sales-backend-baseline.txt; sales-backend-focused-clean.txt; sales-backend-concurrency-final.txt; sales-backend-regression-clean.txt; sales-backend-system.txt; sales-backend-drift-final.txt; sales-backend-graph.txt. Frontend evidence: Gap-Analysis/sales-management-checks/sales-backend-frontend-results.json, sales-api-final.txt, sales-backend-ui-final.txt and sales-backend-build-final.txt, plus backend-check-*.txt.

Implemented but not fully verified: actual browser/native PDF download/print/mobile layouts, deployment target engine and production-scale reporting. Pending/blocked: returned-stock posting, credit GL/receivable posting, nonzero VAT/LED issuance, split credits, authorized correction/reversal, shared migration/grants and live deployment acceptance. Those unsupported operations are not marked working.

## Workspace observations requiring reconciliation

The backend checkout was externally switched to main while tests ran. Both branches referenced the same 3097135 base commit and uncommitted Sales work was intact; the checkout was returned to codex/sales-backend without merging or discarding files.

The shared database hash changed during the session: initial 80d40edad27262fa6fe4af414b20c3e0e7cb569a2ec2b636751f9343617d678e; observed later aca661cefa79246938a2cc340173f22a32ecf6e8b9a34af7d9907d9f3ac95890. Our Django commands explicitly skipped dotenv and used isolated in-memory/disposable file DBs, with outbound sockets blocked. No operational write/migration was executed. Read-only inspection found the file modification time 2026-10-28 17:52:21 IST and newest outstanding JWT token 12:22:21.234440 UTC, strongly correlating the change with token issuance; attribution to a particular process/request is unproven without operator logs. Integrity quick_check is ok; SharedEntry count remains zero, latest ERP migration remains 0020 and no Sales table is installed. Do not overwrite/reset the shared file. Retain this incident as a rollout limitation, not a claim of byte-identical preservation. Evidence: sales-backend-db-preservation.txt and sales-backend-db-timestamps.txt.


Final focused rerun after reporting projection parity and immutable credit-source checks: 12/12 passed (sales-backend-focused-last.txt). The reporting bootstrap includes all invoices for explicitly authorized report-all users, so currency selectors and ledger source sets agree. Credit-note draft edits cannot silently change the originating return. The final frontend build is sales-backend-build-final.txt; final adapter/UI reruns are sales-api-final.txt and sales-backend-ui-final.txt. Approved-return face values remain distinct from actual issued-credit deductions in cards/charts.


Final issuance hardening is covered by the last 12/12 focused run: the tax-policy gate follows the original invoice/order VAT/LED provenance, not just the credit's rounded tax component. A fractional credit whose VAT rounds to zero still cannot bypass the unconfirmed tax-adjustment policy. Missing provenance fails closed. Returned-record and ledger DTOs include their historical linked credit-note references, which the existing detail view now displays. The final race check passed again (sales-backend-concurrency-last.txt). All 28 frontend scripts passed in the final broad rerun (sales-backend-final-frontend-results.json); the last production build passed after the linked-reference display change.
