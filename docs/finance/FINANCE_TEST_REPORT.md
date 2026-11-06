# Finance implementation and test report

## Current restricted increment — technical readiness and CA ownership

6 November 2026, Asia/Calcutta. This section is the current result; the earlier Phase 1 report below remains historical evidence. No new source/configuration change beyond the approved inventory was made.

### Behavior delivered

GET /api/finance/status/ now adds accounting_setup=not_assessed and posting_enabled=false for every authorized diagnostic result. available=false, read_only=true, existing JWT/FinanceEntitlement/action checks and all launch restrictions remain unchanged. Client query parameters cannot change these flags. No new API route, permission, model, migration or accounting-master query was added.

The existing ERPNext dashboard now reuses FinanceReadinessPanel and FinanceSetupGuide. Initial state is Connection not checked; configured/unavailable/denied/timeout/error states remain technical states. The exact CA handoff message appears only after an authenticated HTTP 200 response with validated not-assessed/disabled flags. It disappears when retrying, loading, receiving an error or unmounting on an access change. The guide explicitly says accounting setup has not been assessed; this makes no judgment about the existence/completeness of financial setup.

The manual PowerShell script privately prompts for API key and secret, validates local-HTTP/remote-HTTPS origins and expected nonprivileged identity, and probes only GET ping and get_logged_user. Redirects/proxy/cookies are disabled; response size and cancellation are bounded. It returns normalized states/booleans only, never persists credentials or changes env files, and sanitizes exception/native output. Managed plaintext strings exist transiently for HTTP authentication; cleanup is not a guarantee of secure memory erasure. The script was tested under mocked transport, not against live Frappe.

The CA / Finance Manager owns all accounting/company configuration later. No company, accounts, fiscal years, taxes, openings, bank accounts, financial policy or production sample records were created or assumed. Future independent review, CA sign-off and activation are documented only; there are no functional approval/configuration controls, COA endpoint or financial writes.

### Exact authored-file inventory for this increment (20 files)

Modified backend files:

- backend/finance_gateway/views.py
- backend/finance_gateway/tests.py

Modified frontend source/tests:

- frontend/src/lib/financeGateway.ts
- frontend/src/pages/ERPNextFinanceDashboard.tsx
- frontend/scripts/check-finance-gateway.mjs
- frontend/scripts/check-finance-gateway-browser.mjs

Created source/script files:

- frontend/src/components/finance/FinanceReadinessPanel.tsx
- frontend/src/components/finance/FinanceSetupGuide.tsx
- finance/scripts/Test-FinanceConnectivity.ps1

Updated existing Finance documents:

- finance/docs/FINANCE_INTEGRATION_AUDIT.md
- finance/docs/FINANCE_ARCHITECTURE.md
- finance/docs/FINANCE_API_CONTRACT.md
- finance/docs/FINANCE_DATA_MAPPING.md
- finance/docs/FINANCE_IMPLEMENTATION_PLAN.md
- finance/docs/FINANCE_TEST_REPORT.md

Created design document:

- finance/docs/FINANCE_ADMIN_SETUP_DESIGN.md

Updated frontend development documents:

- frontend/docs/DEVELOPMENT_STATUS.md
- frontend/docs/BACKEND_TODO.md
- frontend/docs/BUSINESS_RULES.md
- frontend/CHANGELOG.md

Build/test-generated dist metadata and dependency caches may refresh locally. New screenshots/logs used temporary storage; existing Phase 1 evidence files were not overwritten. Changes already present relative to Git HEAD from earlier work are not attributed to this increment. Snapshot comparison verified 171 protected files byte-for-byte unchanged, including login/sidebar/routes/authentication, Django accounting/operational sources, settings, env credentials and Docker configuration sources.

### Validation executed in this increment

- Focused backend: python -B test_baseline_runner.py test finance_gateway.tests accounts.test_applications — 27 passed in 34.176 seconds; includes 17 gateway/adapter/script tests and ten existing entitlement tests. Script tests parse the real PowerShell file, use masked synthetic inputs and replace only transport in memory; 15 success/denial/timeout/malformed/unsafe-origin/privileged-identity scenarios make no actual network connection. An initial harness stdin/module issue was corrected; tests also exposed PowerShell singleton-array unwrapping, now explicitly rejected by the script. No real credential was used.
- Full backend: python -B test_baseline_runner.py test finance_gateway.tests accounts accounting erp inventory quotations — 361 tests in 343.847 seconds, 355 passed, six unchanged missing-prepare_quote errors. No additional backend failures. Existing failures are listed in the historical section below. Database=:memory:, dotenv skipped, outbound socket connections blocked. Existing migrations are applied only to disposable test memory; no shared DB is touched.
- Django migration drift: python -B test_baseline_runner.py drift — passed, No changes detected.
- Frontend focused SSR/API/preservation script — passed. Covers all connection states, no CA guide without authenticated state, guide without forms/buttons, strict policy metadata, rejection of old/malformed contracts and inconsistent HTTP/connection states, unchanged route/navigation/login/accounting preservation assertions.
- Focused synthetic Edge desktop/mobile browser — passed. Covers not-configured state, authenticated-only CA guidance, removal of guidance during a delayed retry and timeout, errors/403 sanitization, access revocation, user switch, logout and no availability bypass. Local loopback permission was used for this disposable fixture test only. Mobile screenshot was visually inspected.
- Existing frontend check-auth.mjs and check-led-finance.mjs — passed.
- TypeScript --noEmit --incremental false — passed.
- Production npm run build — passed (1773 modules); existing >500 kB chunk warning remains. No dependencies changed or deployment performed.
- Existing isolated Finance preview tests — seven passed; preview source unchanged.
- Additional unchanged check-finance-browser.mjs — fails at line 96 expecting 370,000.00. Read-only CDP diagnostics confirm the actual synthetic customer profit is 3,70,000.00 (Indian digit grouping), with the expected underlying amount and unchanged Accounting/FinanceInsights code. --lang=en-US and an Intl locale override attempt did not resolve this environment-dependent formatting assertion. This extra browser suite is not fully passing and its later assertions were not reached. No unrelated accounting/test fix was made. This was not part of the previous full browser baseline, so it is recorded as an additional observed existing-script/environment failure rather than silently claimed as a pre-existing passing check.
- Git diff --check — passed for both repositories.
- Protected-source/environment/configuration snapshot: all 171 hashes unchanged.

### Acceptance review — formatting, inventory and safeguards

Read-only acceptance checks confirmed that Accounting.tsx, FinanceInsights.tsx, currencyDisplay.ts and check-finance-browser.mjs are all identical to frontend Git HEAD. The relevant formatter/test source predates the readiness increment (latest relevant tracked change: 7e99a00, 31 October 2026). The test hardcodes Western grouping at scripts/check-finance-browser.mjs:96, while FinanceInsights uses native Number.toLocaleString(undefined, two-decimal options). Therefore the locale-sensitive assumption is pre-existing; no claim is made that a historical run used this same machine locale.

Fresh browser diagnostics, without editing assertions or application behavior, returned navigator.language=en-US but new Intl.NumberFormat().resolvedOptions().locale=en-IN. For 370000 with the same two-decimal options: default=3,70,000.00, explicit en-US=370,000.00, explicit en-IN=3,70,000.00. Number.prototype.toLocaleString is native and the page contains the Indian-formatted amount. The observed failure is a display-locale expectation mismatch, not an accounting calculation change. Browser language alone is not a reliable assertion of the default numeric locale.

No accounting behavior or existing browser test was changed. A future test-only fix should either derive the expected display from the active numeric locale and assert the numeric fixture separately, or explicitly establish and verify the test browser's Intl locale before asserting en-US text. Do not change production formatting to satisfy this test. The unmodified suite still fails at that assertion; later assertions remain unexecuted.

The 20 authored paths listed above were parsed and checked: exactly 20 unique existing files, with no dist/node_modules/tsbuildinfo paths. Frontend Git index has no tracked dist/node_modules/tsbuildinfo files, both repositories have no staged changes, and generated dist/index.html, tsconfig.tsbuildinfo and node_modules/.vite paths are ignored. Current working trees also contain older Phase 1 changes; the complete Git status is not a clean 20-file readiness diff. Finance documentation/source is outside the two inspected repository roots and must be included deliberately in any later approved delivery packaging. No files were staged or committed.

All 171 protected-file hashes still match the pre-readiness snapshot. Focused backend validation was repeated: 27 passed in 36.383 seconds, using in-memory SQLite with dotenv skipped and outbound sockets blocked. Frontend focused SSR/API/preservation and synthetic Edge readiness checks passed again. JWT, explicit entitlement/action checks, denied-request isolation, launch restrictions, available=false, posting_enabled=false, accounting_setup=not_assessed and authenticated-only CA guidance remain intact. No full backend/build rerun was needed because no application source changed during acceptance review; the full-suite/build results above remain the latest runs.

This acceptance review changed only this report and the existing implementation plan. It introduced no new source files or generated delivery files and retains the same 20 authored-file inventory.

### Regressions and remaining prerequisites

No new backend or focused readiness UI regressions remain. The six known backend fixture errors and the additional existing browser numeric-format assertion remain unresolved. Native authentication/company-scoped authorization and production configuration are unverified; no live Frappe probe was executed in this increment.

Before custom functional React Finance configuration screens: verify installed native contracts and private authenticated transport in the actual Django runtime; approve a company-scoped entitlement/action model; define CA preparation/submission/sign-off plus genuinely independent review and separately authorized activation; implement durable revisions/contributors/evidence/idempotency and corresponding backend APIs under separate approval. Future form contracts for fiscal/tax/bank/opening/policy configuration require individual verification; do not infer them from the Account schema or preview fixtures. CA-owned financial setup is later business work, not a prerequisite to testing identity connectivity. Posting remains disabled even after a successful technical check.

No commit, push, deployment, Docker-resource change, shared DB operation, migration addition, credential provisioning or functioning approval action occurred.

---

## Earlier Phase 1 implementation report (historical)

6 November 2026, Asia/Calcutta. Restricted local implementation only. No commit, push, deployment, Docker operation, shared DB migration, production configuration change or secret provisioning.

## Result

Added a permission-controlled ERPNext Finance status shell at `/finance/erpnext` and read-only `GET /api/finance/status/`. ERPNext Finance remains unavailable in every application/launch/status path. `/finance` and all existing accounting behavior are preserved. `/finance/login` and its switch link were retained because actual intended Finance-user common-login/native authorization could not be verified.

Chart of Accounts retrieval, accounting writes and all synchronization remain unimplemented. There are no placeholder routes for them. This result is not a claim that live ERPNext integration works.

## Changed files in this increment

Existing production source/configuration:

- `frontend/src/App.tsx`: imports, provider wiring, new protected route and exact new-route exception to QC redirect. Existing route definitions retained.
- `frontend/src/components/layout/AppShell.tsx`: read entitlement context and append new group; original navigation constants and styling preserved.
- `backend/config/urls.py`: one include for api/finance. Existing auth application/launch lines were already present in the working tree before this increment.
- `backend/config/settings.py`: optional empty server-only diagnostic configuration; no operational settings altered.
- `backend/.env.example`: empty diagnostic setting names and secret placement guidance. No real env file changed.

New production source:

- `frontend/src/context/FinanceAccessContext.tsx`
- `frontend/src/components/finance/FinanceRouteGuard.tsx`
- `frontend/src/pages/ERPNextFinanceDashboard.tsx`
- `frontend/src/lib/financeGateway.ts`
- `backend/finance_gateway/__init__.py`
- `backend/finance_gateway/adapter.py`
- `backend/finance_gateway/permissions.py`
- `backend/finance_gateway/views.py`
- `backend/finance_gateway/urls.py`

New tests:

- `backend/finance_gateway/tests.py`: 14 new tests.
- `frontend/scripts/check-finance-gateway.mjs`: preservation, SSR guards/navigation/shell and API contract assertions.
- `frontend/scripts/check-finance-gateway-browser.mjs`: synthetic Edge interaction and desktop/mobile checks. Uses a disposable browser profile, fixture user and mocked Axios adapter; no live backend/Frappe authentication.

Documentation:

- Six `finance/docs/FINANCE_*.md` deliverables: audit, architecture, API contract, data mapping, implementation plan and this report.
- `frontend/docs/DEVELOPMENT_STATUS.md`, `frontend/docs/BACKEND_TODO.md`, `frontend/docs/BUSINESS_RULES.md`, `frontend/CHANGELOG.md`, `backend/README.md`: concise scoped additions.
- `finance/docs/evidence/increment-baseline.json`: pre-edit hashes of protected files plus existing route/navigation definitions.
- Evidence logs: backend-regression.txt, backend-focused.txt, frontend-focused.txt, frontend-browser.txt, frontend-build.txt and validation-summary.txt. Browser screenshot: finance-shell-mobile.png.
- Generated local frontend dist/build metadata and dependency caches may be refreshed by the approved build/test commands; no generated output was deployed or committed.

The backend working tree already contained prior entitlement/auth documentation/model/migration changes relative to Git HEAD. These were inspected and preserved; they are not attributed to this increment. Preservation hashes verify the relevant unchanged working-tree source, rather than assuming a clean checkout.

## Reused functionality

Existing Django JWT/session/profile, default throttling and RolePermissionBackend; FinanceEntitlement and finance_eligible; existing accounts.view_financeentitlement permission; no-store response pattern; existing React AuthContext, Axios client, AppShell/NavLink/Card, typography and styling; existing accounting posting/idempotency/audit/dispatch code unchanged.

## API and migration inventory

Added only GET `/api/finance/status/` (HEAD/OPTIONS standard read-only behavior; other methods rejected). JWT + active eligible FinanceEntitlement + accounts.view_financeentitlement required. 200 means expected integration identity answered; 503 means not configured/native denied/timeout/unavailable/invalid response. All responses retain available=false. No native errors, identities, URL, cookies or secrets are forwarded. Error responses also disable caching.

No existing API behavior was modified. Applications and launch remain unchanged. No migration 0005 was created: the existing view_financeentitlement permission covers sensitive grant-related diagnostics. No grants were seeded, no DB record or model schema changed, and no migration was applied to shared storage. Tests apply existing migrations only inside disposable memory databases.

## Tests executed

Pre-change audit baseline: `python -B test_baseline_runner.py test accounts accounting erp inventory quotations`: 344 tests in 358.261 seconds, 338 passed, six existing errors. In-memory SQLite, dotenv skipped and outbound sockets blocked. The earlier run's output is in chat tool execution; historical matching failures are also recorded in NAVAPACK_ERP_AUDIT_REPORT/backend-tests.log and the older common-login baseline report.

Focused backend: `python -B test_baseline_runner.py test finance_gateway.tests accounts.test_applications`: 24 tests passed (14 new gateway/adapter + ten existing entitlement tests). Tests exercise actual JWT enforcement, inactive/expired/revoked/blank/privileged mappings, Admin/superuser without entitlement, entitlement without action permission, existing role-assigned permissions, no unauthorized upstream call, rejection of writes, no accounting/grant mutations, no availability/launch bypass, fixed authenticated GET, HTTPS rules, invalid config, denied/redirect/server/malformed/oversized results, timeouts and sanitized/no gateway logging. The focused run was repeated after final gateway response-header changes; final evidence is backend-focused.txt.

Broader post-change backend: `python -B test_baseline_runner.py test finance_gateway.tests accounts accounting erp inventory quotations`: 358 tests in 358.459 seconds, 352 passed, six existing errors. Includes authentication, accounting, dispatch settlement, Sales, production/QC, inventory, quotations and in-memory forward/reverse migration regression coverage. System check: no issues. Evidence: backend-regression.txt. The later response-header change is covered by the final focused rerun; no operational code was edited afterward.

Migration drift: `python -B test_baseline_runner.py drift`: passed, No changes detected. Existing operational/shared databases were never opened by this runner.

Frontend focused: `node scripts/check-finance-gateway.mjs`: passed. Verifies byte hashes for login/AuthContext/API/session and core backend accounting/eligibility files, original sidebar constants, every existing route definition, unauthorized/loading/error content blocking, entitled/unavailable shell, Admin/Accounts/receptionist/QC nav visibility and strict GET-only client contract. Expected React Router SSR useLayoutEffect warnings are filtered only inside static shell rendering; other errors remain visible.

Frontend existing regressions: `node scripts/check-auth.mjs` and `node scripts/check-led-finance.mjs`: passed. Existing scripts reported a local HMR-port-in-use warning during one run; assertions completed successfully. No existing service was stopped or changed.

TypeScript: `node node_modules/typescript/bin/tsc --noEmit --incremental false`: passed. Production: `npm run build`: passed; Vite reports a chunk above 500 kB. No broad bundle refactor was made. Existing dependency/package versions were not changed.

Browser: `node scripts/check-finance-gateway-browser.mjs`: passed in headless Edge with authorized local loopback access. Covers no automatic diagnostics, diagnostic 503/403 and secret suppression, access outage/retry/loading, entitlement revocation on focus, canceled stale first-user grant after account switch, logout, availability=true fixture still unavailable, and mobile overflow. Synthetic user/JWT/API fixtures only. Mobile screenshot was visually inspected. Sandbox CDP WebSocket access failed initially; a pipe attempt timed out. A first loopback retry exposed a test-harness CommonJS dependency setup issue; explicit optimizer entries fixed the harness, and the final run passed. These initial test-environment/harness failures are not product regressions.

Finance preview baseline from audit: `node --test scripts/finance.test.mjs` in finance/frontend: seven tests passed. Preview source remained unchanged and is not imported into the production app.

## Existing failures versus regressions

Both full baseline and post-change runs fail exactly these WorkflowBaselineTests with AttributeError: missing prepare_quote:

- test_job_wrong_saved_unit_rejects_issue_without_stock_change
- test_legacy_json_orders_and_jobs_read_without_new_control_fields
- test_multi_material_failure_rolls_back_earlier_movement_and_job_json
- test_quote_lineage_is_preserved_in_entry_and_top_level_order_fields
- test_two_manufacturing_lines_and_stock_share_one_snapshot_parent
- test_unsafe_current_draft_allocation_and_same_corrector_approval

No new backend failures were observed. Existing tests were not edited to hide these failures. Frontend final focused/browser/typecheck/build checks passed; production chunk size remains a warning. No claim is made about PostgreSQL concurrency, deployed UI, real Finance-user login or live ERPNext accounting correctness.

## Remaining dependencies and assumptions

Live Finance hostname/loopback access failed in the read-only audit environment. No authenticated live endpoint or credential was verified or retried using elevated access. Local browser loopback permission was used only for synthetic UI testing. Existing user-run Docker metadata evidence is not live confirmation. Company/currency/COA, native company/data scope, least-privilege integration identity and per-user access remain unverified. Real Finance users' grants and existing migration deployment remain unverified because shared storage was not inspected.

Status-only service-identity matching does not prove native authorization or completion of SSO. No application flag may be enabled from this result. Follow FINANCE_IMPLEMENTATION_PLAN.md for safe optional manual Docker diagnostics and the next separately approved read-only authorization/COA phase. All posting/synchronization needs approved ownership/cutover and reconciliation before implementation.
