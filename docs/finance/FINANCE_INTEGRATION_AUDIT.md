# Finance integration audit

Audit: 6 November 2026, Asia/Calcutta. Restricted local increment authorized after the read-only audit. Source inspection and automated tests establish local behavior only. Existing reports containing user-supplied Docker evidence are historical evidence, not live verification by this increment.

## Restricted readiness increment — current scope

The subsequent approved local increment changes only status policy metadata, the Finance dashboard/client/tests, two reusable Finance panels, a private manual connectivity script and the listed documentation. Login, sidebar, routes, operational/accounting source, settings, credential files, models/migrations and Docker resources are unchanged in this increment.

Technical readiness and accounting readiness are separate. The client’s CA / Finance Manager owns all financial/company setup later; no company, Chart of Accounts, fiscal year, tax, opening balance, bank or accounting policy is assumed or created. Existing master data is not assessed. Status always returns accounting_setup=not_assessed, posting_enabled=false and available=false. Technical identity verification alone only permits the informational CA guide to appear.

Remaining technical dependencies: confirm Django runtime/network path; verify dedicated least-privilege identity and private credentials; verify native installed API behavior; preserve local entitlement/action controls; design company-scoped authorization and separate future Finance setup permissions. Company/accounting setup is deferred business work, not a blocker to testing authenticated identity. The older inventory below remains historical/source evidence; its company/COA verification gaps must not be interpreted as instructions to configure them now.

Independent operational maker/checker patterns exist, but no Finance configuration revision/approval/activation model or endpoint exists. Those patterns cannot be applied to Finance by simply relabeling an Admin role. The new setup-guide panel is informational, without working setup/approval actions.

## Frontend inventory

- `frontend/src/main.tsx`: React 18, BrowserRouter, shared root application and stylesheet.
- `frontend/src/App.tsx`: nested Routes under RequireAuth/AppShell; role-specific QC restrictions, module guards and accounts landing page. `/finance` serves AccountingPage; `/finance/quality` is an existing accounts-only summary. `/sales` includes ledger, returns and credit notes. Operational quotation/order/job/stock routes are retained.
- `frontend/src/pages/Login.tsx`, `components/login/*`: existing Navapack login and branding. A pre-existing finance prop renders a separate `/finance/login` variant and a switch link. No login file or component was changed.
- `frontend/src/context/AuthContext.tsx`: Django login/profile restoration, effective role mapping, generation-based stale-session protection, opt-in mock mode and logout. It contains an accounts-role restriction on the pre-existing finance login variant; this is not ERPNext entitlement.
- `frontend/src/lib/authSession.ts`, `lib/api.ts`: tab/persistent JWT storage, Axios bearer injection, rotating refresh, `/auth/login/`, `/auth/me/`, `/auth/refresh/`, configurable API base. Current source fallback is a remote API address; no deployed configuration was changed. Finance uses the shared client. The existing interceptor refreshes `/auth/me/` but excludes other auth-prefixed endpoints; an expired applications request may require retry after session renewal.
- `frontend/src/components/layout/AppShell.tsx`: role-specific NAV_ITEMS/FINANCE_NAV_ITEMS, NAV_GROUPS, QC navigation, SalesNavigation, existing sidebar markup and styling. Original constants remain intact; only an explicitly entitled ERPNext Finance group is appended.
- `frontend/src/pages/Accounting.tsx`, `FinanceDashboard.tsx`, `FinanceMasters.tsx`, `FinanceLedgerList.tsx`, `FinanceAnalysis*.tsx`, `FinanceInsights.tsx`, `FinanceProfitLoss*.tsx`, `FinanceValuationItem.tsx`, `FinanceCurrencies.tsx`: existing Django accounting UI, distinct from the new ERPNext shell. The initial audit inventoried these files; it did not line-review every financial screen.
- `frontend/src/context/InvoicesContext.tsx`, `lib/invoices.ts`, `pages/SalesOrders/SalesInvoice.tsx`, `pages/Sales/SalesWorkspace.tsx`, `SalesDocuments.tsx`, `CreditNotePrint.tsx`: operational invoice register, Sales reporting and return/credit UI. Existing source identity and totals remain unchanged.
- `frontend/package.json`, `tsconfig.json`, `vite.config.ts`, `scripts/check-auth.mjs`, `check-led-finance.mjs`, `check-finance-browser.mjs`: TypeScript/Vite build and Node/Vite SSR/assertion checks; no general npm test/lint script. Browser scripts use synthetic fixtures, not persistence evidence.

## Django inventory

- `backend/accounts/models.py`, `backends.py`, `views.py`, `management_api.py`: custom user, active assigned roles, Django permission backend, SimpleJWT login/profile and existing role administration. No changes to these files.
- `backend/accounts/migrations/0004_financeentitlement.py`: explicit one-to-one grant, disabled by default, mapped Frappe identity and optional expiry. The audit did not open the operational DB to verify migration deployment or grants.
- `backend/accounts/applications.py`, `test_applications.py`, `tests.py`: eligibility rechecked from storage; rejects missing, disabled, expired, inactive, blank, Guest and Administrator mappings. `/api/auth/applications/` exposes entitled but always unavailable; `/api/auth/finance/launch/` returns 403 or 503 and never issues a session. Unchanged.
- `backend/config/settings.py`, `urls.py`: default JWT authentication, authenticated API policy, throttling, role backend, secure production defaults and API registrations. Current operational DB configuration is SQLite. Tests bypass dotenv and use in-memory SQLite.
- `backend/accounting/models.py`: Book, Account, Party, Document, DocumentLine, JournalLine, Allocation, CostAssignment, StockValue, ValueMovement, PostingEvent and finance masters. Document command/digest/source uniqueness and append-only evidence exist.
- `backend/accounting/services.py`, `integration.py`, `dispatch_sales.py`, `signals.py`, `apps.py`: atomic local posting, immutable posted records/reversals, durable local posting events and existing operational posting hooks. Cash/credit dispatch can already post an invoice and receipt; raw movements and dispatch also trigger valuation. Do not add a second accounting destination to these paths before cutover design is approved.
- `backend/accounting/api.py`, `views.py`, `urls.py`: accounting access/schema checks; setup, commands, reports, queue, supplier bills, source capture, period closing, PDF and tax/account mapping APIs. Existing legacy accounting Admin access is distinct from new ERPNext entitlement; unchanged.
- `backend/erp/models.py`, `sales_models.py`, `sales_service.py`, `operational_records.py`: SharedEntry invoices, immutable EntryRevision, CommandReceipt and production evidence, SalesReturn/SalesCreditNote and inspections. Credit issuance is operational; gl_status is not proof of an ERPNext posting. Confirmed dispatch requires matching stock OUT, approved order and quantity/QC checks.
- `backend/test_baseline_runner.py`, `requirements.txt`, existing accounting/Sales/operational/inventory/quotation tests: safe runner suppresses dotenv, forces an in-memory DB and blocks outbound sockets. Tests never use shared DB configuration.

## ERPNext inventory

- `finance/docker/compose.finance.yaml`, `upstream/compose.yaml`: project-scoped ERPNext services with DB, Redis, workers, scheduler and proxy; only loopback port 8082 published by the Finance override.
- `finance/docker/Containerfile.app`, `apps.json`, `versions.local.json`: Frappe 16.55.1, ERPNext 16.53.0, custom app 0.0.1, upstream commit `fbf76edcf5791b1d9743955b85caf0a19974d5a6`; MariaDB 11.8.5, Redis 8.6.0-alpine and Python 3.14.0 images are digest-locked in the local version file. Image recipes contain local tags; this is not a complete hermetic build guarantee.
- `finance/docker/Phase1A.ps1`, `scripts/site.py`, `scripts/verify.py`: staged manual setup/verification tooling. No Docker command or helper phase was executed in this increment. No Docker file was modified.
- `finance/backend/navapack_finance/pyproject.toml`, `navapack_finance/__init__.py`, `hooks.py`: custom source package exists; hooks are generated scaffold comments, without active Finance APIs or posting integration.
- `finance/frontend/src/App.tsx`, `api/frappe.ts`, `api/types.ts`, `data/demo.ts`, `scripts/finance.test.mjs`: isolated fictional preview. The Frappe provider deliberately throws; listed native resource strings are not working transport. No preview component or credential is imported into production React.
- `finance/docs/PHASE-1A-DOCKER-REPORT.md`, `PHASE-1A-MANUAL.md`, `finance/COMMON-LOGIN-IMPLEMENTATION.md`, `PHASE-1A-ENVIRONMENT-2026-11-05.md`, `frontend/docs/finance-integration-review.md`, frontend/backend README files: inspected historical implementation evidence. Some statements describe older preparation-only states; later report sections record user-run metadata verification and an unresolved asset manifest/login issue.

Live constraints: `finance.localhost` DNS failed in this environment. A Host-routed 127.0.0.1:8082 ping failed with socket access denied. The deployed Vercel login was inaccessible through the web tool. No live API key, integration identity, company, reporting currency, chart, company permissions or per-user authorization was verified. No secret env file was printed. A historical metadata/ping pass does not verify accounting reports or authorization.

## Gap classification after this increment

- Implemented and verified locally: existing JWT regressions and Finance eligibility; status permission checks, fail-closed navigation/route boundary, unavailable dashboard, read-only diagnostic adapter under mocks, errors/response limits and preservation checks. See test report for scope and limits.
- Implemented but unverified live: Docker/custom-app installation and metadata reported in prior user-run evidence; configured runtime credentials, identity and live status transport remain unverified.
- Partially implemented: one-login transition (existing main login works in regression tests, intended real Finance users unverified); role UI remains an operational policy rather than ERPNext authorization; the existing finance login variant remains deliberately preserved.
- Missing: approved production deployment, verified per-user authorization/company scope, operational-to-ERPNext mappings, ERPNext document links/posting state, cross-system outbox/idempotency/reconciliation and financial operation audit integration.
- Blocked by external dependency: authenticated live connectivity and least-privilege identity verification, company/currency/COA verification, native authorization checks and browser asset/login repair confirmation.
- Explicitly deferred: Chart of Accounts retrieval, all ERPNext accounting writes, master synchronization and all native ledger/statements/report APIs. No placeholder routes were added for these features.
- Existing failure: six WorkflowBaselineTests fail because prepare_quote is absent. They were present in the pre-change run and historical logs; unrelated fixtures remain unchanged.

## Transition decision

Retain `/login`, `/finance/login`, the login switch link and `/finance` exactly as existing source. Add only `/finance/erpnext`. The main login is already exercised for operational role profiles, but the actual intended Finance users and native authorization have not been verified. Redirect/removal requires that verification in a later approved increment.
