# Common-login prerequisites — 5 November 2026

**Status: blocked on ERPNext/Frappe and a verified server-side SSO deployment. Common login is not complete.** No demo authentication has been substituted. The existing Finance app remains a clearly labelled local preview.

## Prerequisites verified

- Existing operational repositories were clean at the start of this task. `frontend` and `backend` are separate Git roots. Finance preview lives at `F:/WORK/ERP/finance/frontend`, outside those two repositories.
- Existing React login sends credentials through `AuthContext` and `lib/api.ts` to Django `/api/auth/login/`. It restores profiles through `/api/auth/me/`; rotating JWT refresh is shared across requests. Browser sessionStorage/localStorage handling is unchanged. The requested future prohibition on exposing Django JWTs through the Finance handoff is respected by issuing no handoff at all; the existing operations JWT architecture has not been converted to cookie authentication.
- Django has one assigned operational role per user plus a legacy role field. Active assignment controls `effective_role`. The profile serializer supplies a single role. Existing role codes include Accounts, Admin, Sales/receptionist, Project Manager, Production, Store, operators and QC. Role and user management remain unchanged.
- Existing `/finance`, `/finance/quality` and `/finance/login` routes and Django accounting transactions already exist. No accounting source, posting code, ledger, routes or data were changed.
- Read-only WSL verification: `Navapack-Finance` is running, but `command -v bench` returned no executable, the planned `/home/nidhin/navapack-finance/navapack-bench` directory is absent, no `apps.txt` was found under the home directory to depth five, and listeners are DNS only. The Z: mount warning does not prevent these checks. This demonstrates that the inspected target is not ready; it is not a claim about every possible remote installation.
- No remote ERPNext origin or configured broker/IdP was supplied or found in inspected integration code. No installed Frappe release, site, Finance identity or enabled native permission set could be verified. Therefore no undocumented Frappe authentication endpoint was invented or called.
- Read-only aggregate query of local Django SQLite: 1 user, 1 active user, 0 legacy or assigned Accounts-role users, and no Finance entitlement table before this task. No usernames, password hashes or secrets were printed. This is local state, not evidence about deployed Django identities.
- Deployment config: operational `vercel.json` rewrites all routes to `index.html`; there is no Finance mount or API proxy declaration. Django settings inspected locally allow only development frontend CORS origins. Deployed Vercel/Django configuration was not accessed or changed. Local Finance hash routing on port 5180 is not a deployed SSO origin.

## Implemented independently testable prerequisites

Added `FinanceEntitlement`, an explicit one-to-one local grant with Frappe identity mapping, disabled-by-default flag, optional expiry and update timestamp. The migration only creates the new table. It seeds no users, assignments, grants or permissions. It has been applied only to disposable in-memory test databases.

The grant is eligibility to attempt a future Finance launch, not Frappe authorization or proof of a native identity. There is no implicit grant for Admin, superuser, staff or Accounts. Empty, Administrator and Guest mappings are rejected. Grants are queried afresh on every request; disable and expiry are not frozen into login tokens. No client-writable grant-management API was added.

Added authenticated endpoints:

- `GET /api/auth/applications/`: returns existing-role operations visibility and Finance local eligibility, with Finance `available: false` and reason `frappe_sso_not_configured` unconditionally. This does not replace operational endpoint permissions.
- `POST /api/auth/finance/launch/`: anonymous callers get 401, ineligible callers get 403, eligible callers get 503 until real SSO exists. No session, authorization code, redirect, cookie, secret or Frappe identity is returned. Submitted redirect URLs and role/identity claims are ignored. Personalized responses are marked `Cache-Control: no-store`.

These endpoints use existing Django authentication for local identity checks only. They never forward its JWT to Frappe. There is deliberately no configuration switch that turns missing SSO into successful authentication.

## What is deliberately not claimed or implemented

No common-login redirect, application chooser or switcher is wired into React while launch is unavailable. No existing frontend source was changed. Login layout, branding, fields, behavior and existing link therefore remain byte-for-byte untouched by this task. The user required removing the existing separate-login link **after** common login is verified; that condition has not been met. The legacy separate route is retained pending that verified cutover; no additional Finance login was added.

Finance-only operational denial, multi-application selection, native Frappe company authorization, native disabled-user checks, per-user Finance sessions, Finance logout/session expiry and live dashboard/Account/General Ledger remain blocked. Local grant revocation is tested, but cannot revoke a Frappe session that does not exist. No arbitrary JWT-to-Frappe exchange or shared administrator access was introduced. Native report agreement and financial-data accuracy cannot be tested without a site. Existing previews never become live data automatically.

## Files changed

Existing backend files: `accounts/models.py` (new independent model), `config/urls.py` (two new auth routes), `README.md` (status reference).

New backend files: `accounts/applications.py`, `accounts/migrations/0004_financeentitlement.py`, `accounts/test_applications.py`.

New report: `finance/COMMON-LOGIN-IMPLEMENTATION.md`. Evidence logs are in `finance/frontend/docs/common-login-*.txt`.

No existing frontend or Finance React source, role assignment, operational accounting transaction, database row or deployment config was changed. No installation, production migration, deployment, commit or Git push was performed.

## Testing

Commands use the existing disposable `backend-test-venv` and `test_baseline_runner.py`, which suppresses dotenv, overrides the database to in-memory SQLite and blocks outbound sockets. No configured database is opened by those tests.

- Focused `accounts.test_applications`: **10 passed**, including anonymous rejection, no automatic grants, disabled defaults, expiry/disable/revocation, privileged/blank identity rejection, malicious redirect input, fail-closed 503 without cookies/redirects, no self-grants, and password login/profile checks for Admin, Sales, Production, QC and Accounts test identities.
- Django system checks: passed, zero issues. Migration drift check: passed, no changes detected.
- Operational frontend production build and existing auth, baseline API contracts, accounting rendering, QC, quotations, production error and Sales management checks: **build and all seven checks passed**. Vite emits its existing large-chunk advisory. These are source/unit/SSR checks, not production/browser login tests.
- Full backend accounts/erp/inventory/quotations/accounting regressions: **344 tests run in 310.647 seconds; 338 passed, 6 errors**. All six errors reproduce on unchanged HEAD as described below. This count includes the ten new entitlement tests. No additional failures were reported.

### Requested acceptance cases

1–4. Admin, Sales, Production and QC credential login: passed against disposable Django test identities; production/browser accounts were not exercised.

5. Finance-role credentials in the same Django endpoint: Accounts test identity authenticates successfully; routing to Frappe is blocked.

6–7. Finance arrival and real Frappe session: blocked, not simulated.

8. Native unauthorized company/report rejection: blocked without native users/site; local eligibility denial alone does not verify it.

9–10. Cross-system session isolation, Finance logout and native session revocation: blocked without a Finance session. Existing operations refresh/logout client regression and new local-grant revocation tests pass.

11. Direct new Finance launch requests: 401/403/503 denial tested. The pre-existing local demo URLs continue to disclose fictional data behind their preview acknowledgement; they are not an authenticated production workspace.

12. Operational regressions: frontend checks pass; backend results are recorded below. No production workflow guarantee is inferred from unit tests.

13–14. Native Chart of Accounts / General Ledger matching: blocked without ERPNext. No fixture is presented as native data.

## Required next steps and deployment requirements

1. Provide an accessible ERPNext/Frappe site and identify installed versions. Installation is outside this implementation and has not been attempted. Supply non-secret origin/site details and approved server access, not administrator tokens in chat.
2. Verify native identities and least-privilege user/company/DocType/report permissions. Explicitly approve grants and any Finance-only operational access policy; do not infer Finance permission from operational roles.
3. Validate a standard supported SSO integration against that release and the Django identity authority. Frappe documents OAuth and REST APIs, but documentation alone does not prove compatibility with this uninstalled target. Prefer a server-side BFF and standard compatible identity flow. If a custom adapter is necessary, review the actual installed implementation before choosing endpoints.
4. Implement and test real identity exchange, issuer/audience/expiry/state/nonce validation, one-use replay prevention, fixed redirects, secure HttpOnly cookies, CSRF, logout, session expiry and revocation. Recheck native user status and Frappe permissions; never use a shared Administrator or browser secrets. Add negative tests for replay, cross-company access and native user disablement.
5. Wire minimal approved frontend routing and multi-application selection only after launch works; preserve purchasing/costing/quotation access and all operational routes. Remove the old separate-login link only when the requested login/session checks pass.
6. Connect native read-only Account and General Ledger/report APIs through the authenticated server adapter. Compare returned values with native ERPNext. No fallback to fictional records in live mode; no posting functionality.
7. Decide HTTPS Finance/ERP origins and proxy/BFF routing, cookie scope, trusted origins/CORS/CSRF and operational/Finance session isolation. The existing static SPA rewrite alone cannot implement secure server-side SSO.
8. Before any deployment, run the new migration in the approved environment, provision explicit grants through an authorized administrative process, verify operational regressions and all 14 requested end-to-end acceptance cases. Deployment and Git push require separate approval.

References checked: [Frappe REST API](https://docs.frappe.io/framework/user/en/api/rest), [Frappe OAuth 2](https://docs.frappe.io/framework/user/en/guides/integration/rest_api/oauth-2). These describe framework capabilities; they are not evidence that SSO is installed here.

## Regression failure provenance

Six errors in `erp.test_baseline_characterization.WorkflowBaselineTests` reproduce on both the modified backend and an untouched `git archive HEAD` export. Each raises `AttributeError: 'WorkflowBaselineTests' object has no attribute 'prepare_quote'` in the existing workflow test helper. The isolated class run executes 8 tests with 6 errors on each revision. This task did not modify those files or repair unrelated tests.

The read-only baseline export and archive are retained under `finance/common-login-baseline-check` for reproducibility; it contains tracked source only, no operational `.env` or database. Its tests also use isolated in-memory SQLite with network blocked. Compare `common-login-workflow-errors.txt` and `common-login-baseline-workflow-tests.txt` in the evidence directory.
