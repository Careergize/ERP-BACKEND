# Finance implementation plan

6 November 2026. Scope: approved restricted local increment only.

## Completed increment

Reuse existing login, operational routes, AppShell, JWT client, FinanceEntitlement and Django permissions. Add `/finance/erpnext` with an entitlement boundary and explicitly unavailable shell. Add a manually requested read-only Django status diagnostic and server adapter, with no enable flag. Retain `/finance`, `/finance/login` and the login switch because actual Finance-user common-login/native authorization is unverified. No model/migration or seeded grant was needed.

The diagnostic action reuses accounts.view_financeentitlement. Any future financial action needs a separately reviewed action permission; this grant-inspection permission must never authorize posting, account access or financial reports. Live native authorization checks remain mandatory.

No ERPNext accounting write, COA retrieval, automatic data migration, browser Frappe authentication, Docker operation, shared DB migration, secret provisioning, service change, commit, push or deployment was performed.

## Revised technical increment — CA-owned accounting

The client’s CA / Finance Manager will configure company, Chart of Accounts, fiscal year, taxes, opening balances, bank accounts, accounting policies and production financial masters later. No defaults, sample records, account selection or financial setup assessment are performed by this technical increment. Their absence or presence is not inferred from an authentication result.

Implemented locally: neutral accounting_setup=not_assessed and posting_enabled=false on the existing status API; reusable React connection-status and setup-guide components; private manual connectivity script. All availability/launch restrictions remain unchanged. The guide says “Awaiting CA configuration and approval” only after a validated authenticated diagnostic response; loading, error, denial, timeout, not-configured and unchecked states do not show that message. No setup forms, approval actions or financial data APIs exist.

Technical prerequisites remain runtime reachability, a dedicated non-Administrator native identity, private server credentials, explicit Django entitlement/action permission, pinned runtime verification and eventual company-scoped authorization design. Accounting setup is not required to test the identity endpoint. No new identity, credentials, environment values or native permission records are provisioned here.

The manual script is `finance/scripts/Test-FinanceConnectivity.ps1`. Run only when authorized to issue read-only diagnostic requests:

```powershell
Set-Location 'F:\WORK\ERP'
.\finance\scripts\Test-FinanceConnectivity.ps1 -BaseUrl 'http://127.0.0.1:8082' -ExpectedIdentity '<existing nonprivileged integration email>'
```

API key and secret are entered through separate masked prompts. Do not pass them as arguments or paste them into chat. The script does not read/write env files, create credentials, save headers or print native bodies/errors. It uses fixed GET ping and identity endpoints, no redirects/cookies/proxy, bounded response size and cancellation, and returns only normalized technical flags plus the disabled/not-assessed policy. Secret strings exist transiently in process memory for HTTP authentication; cleanup drops references/disposes resources, but cannot guarantee erasure of managed string memory. Ordinary server access logs/caches may record requests; the script issues no configuration or financial mutation. It is not a CA readiness assessor or a substitute for the Django entitlement boundary.

After a separately approved live check, record sanitized technical evidence. Do not enable Finance or remove existing login routes from that evidence. Design native read-only pages using version-verified contracts, then verify live field/permission behavior in the approved scope before implementing their Django endpoints. Chart of Accounts retrieval is still deferred.

The Finance Administrator experience is design only; see [FINANCE_ADMIN_SETUP_DESIGN.md](FINANCE_ADMIN_SETUP_DESIGN.md). Explicit preparation, submission, independent approval, CA sign-off and activation permissions/evidence must exist before functional controls. CA approval alone never activates posting. No new migration or approval model is included here.

## Next phase proposal — secure authentication and company-scoped read access

Plan only; not authorization to implement, provision identities/secrets, create scopes in shared storage, migrate or change services. No company, accounts, periods, taxes, opening balances, bank configuration or transactions will be created. The client’s CA will configure accounting later through the future approved Navapack React experience.

### 1. Establish technical identity and transport

Confirm the actual Django runtime and its route to the existing Finance proxy. For Windows-hosted Django, verify the existing 127.0.0.1:8082 proxy/site route; other runtimes need their own reachability check. Do not broaden the adapter's HTTP allowlist or change Docker networking as an incidental fix. Use the existing private diagnostic script and status endpoint; record sanitized identity-match and connectivity evidence only.

Verify a dedicated non-Administrator, non-Guest integration identity and least-privilege credentials. Keep secrets exclusively in an approved private server runtime/secret store. Any new identity, role grants, secret provisioning or environment/service restart requires separate approval. Use authenticated GET only, fixed endpoints, HTTPS remotely, no redirects, bounded timeouts/body limits and sanitized logs/errors. Status-only identity authentication does not require accounting data to exist and does not establish native company permissions.

### 2. Decide and document the identity model

The current adapter authenticates a service identity, not the individual FinanceEntitlement.frappe_user. The existing grant mapping alone cannot prove that the individual native user exists, is active or has company access. Review the existing SSO-oriented launch contract separately; do not declare per-user authorization or SSO complete from service authentication.

For a service-identity design, explicitly authorize Django as the per-user/company policy boundary and restrict the native service account to the approved maximum read scope. A delegated per-user identity design needs separate verified native credentials/session handling. Choose one coherent model before exposing company data. No Django JWT is sent to Frappe as native authentication.

### 3. Define an explicit company authorization boundary

Design a server-side grant keyed by Navapack user, environment/site, exact native company ID, permitted read actions, active/expiry state and approving authority. Existing FinanceEntitlement remains mandatory; its current schema contains no company scope. A new action permission is required for real company-data access rather than reusing grant-view diagnostics permission. Any model/migration needed for durable scopes must be proposed and approved separately; none is added by this plan.

The allowed scope is the intersection of the explicit Django grant, approved site/company allowlist and native integration-user permissions. Do not infer it from Admin/Accounts roles, name similarity, default company, the first returned company, a browser parameter or a pre-existing company record. Native All/Guest/Desk User roles, custom permission overrides and shares must be reviewed. Scope cannot grant access merely because a company exists.

If the CA has not created/selected an approved company, keep scope empty and data access denied/unconfigured. Do not create a temporary company or fabricate values to exercise the UI. Lack of company scope is not an accounting assessment. Synthetic multi-company fixtures belong only in disposable tests.

### 4. Implement a real minimal read-only context contract only after approval

A candidate future GET /api/finance/context/ may return only explicitly authorized company summaries, selected context and neutral readiness flags. It is a proposal, not a registered placeholder endpoint. Before implementation, verify the installed Company's native fields and REST behavior, enforce a dedicated Django action permission and validate returned IDs against scope.

No configured scope means no upstream company request and a sanitized company_scope=not_configured state, without global enumeration. Forged, expired or unauthorized company selection is denied before upstream access. An authorized request must query only approved native company IDs using permission-aware REST, fixed field allowlists and bounded pagination; reject unexpected/out-of-scope records instead of forwarding them. Verify native denial/revocation before marking a company accessible. Preserve nullable/unknown values without invented default currency or accounting settings. Prefer a single approved company initially; multi-company selection needs explicit scoped grants and tests.

No Chart of Accounts or financial document retrieval is included in this phase. available=false, posting_enabled=false, accounting_setup=not_assessed and existing launch restrictions remain constant even after authenticated company reads. Native authorization failure cannot enable availability or be mislabeled as CA configuration failure.

### 5. Test scope and failure behavior before live reads

Use synthetic users/companies in disposable test databases and mocked native responses. Cover missing JWT/entitlement/action permission/scope, Admin without explicit grants, expired/revoked users/grants, two-user/two-company isolation, forged company IDs, remote filter violations, pagination leakage, native identity disabled or permissions revoked, timeout/redirect/malformed response, missing/deleted/renamed company mapping, log/response secret suppression and no Frappe call on denied access. Recheck grants on every request; do not retain stale company access after logout or account switch.

Retain protected-source snapshots, existing operational/accounting and dispatch/idempotency tests, new readiness UI tests, TypeScript/build and no-migration-drift checks. Keep the pre-existing numeric-locale test defect and six workflow fixture errors visible; never change business formatting or weaken authorization assertions to obtain green results.

### 6. Approval and acceptance gates

First approve exact source changes and any proposed authorization schema. Separately approve identity/permission/secret provisioning, shared-storage migrations and any runtime/service changes. Then perform manual authenticated read-only verification from the actual Django runtime. Accept only after no unauthorized cross-company reads, no secret exposure, no accounting/operational changes and all readiness/launch locks remain enforced. No commit, push or deployment is authorized by this plan.

Future CA React configuration forms still need their own verified native contracts, action permissions, durable revisions/contributor evidence, independent review and CA sign-off. No financial configuration or posting is implemented as part of company-read authorization.

Sources reviewed: [Frappe REST authentication](https://docs.frappe.io/framework/user/en/api/rest), [Frappe v16.55.1 permission implementation](https://github.com/frappe/frappe/blob/v16.55.1/frappe/permissions.py), [ERPNext v16.53.0 Company schema](https://github.com/frappe/erpnext/blob/v16.53.0/erpnext/setup/doctype/company/company.json). These establish versioned contract/design evidence, not live site readiness.

## Later phases, documentation only — subject to CA decisions

- Master synchronization: Customer/Supplier identities, Item/UOM variants, account/cost-center/tax mappings, ownership, approvals, deterministic conflict handling and inactive-record behavior.
- Accounting cutover: reconcile existing Django journals/invoices/payments, approve opening balances/cutover scope and disable competing authoritative posting for that scope. Keep history immutable and require a rollback/runbook that cannot double-post.
- Transaction delivery: source eligibility, independent approval, atomic outbox, immutable posting identity/digest, durable attempts, worker leases, external-reference uniqueness and retry/reconciliation controls.
- Sales Invoice and Purchase Invoice posting: approved snapshots, exact mappings/totals and no stock update for Django-owned physical inventory.
- Credit Notes: return linkage, independent review, approved tax/LED treatment, original submitted invoice and separate physical disposition.
- Payment Entries: actual cash/bank account, approved allocations, FX, advances, refunds/reversals and duplicate cash prevention.
- Journal Entries: balanced approved changes, period close, account restrictions and maker/checker separation.
- General Ledger, AR/AP, Trial Balance, P&L and Balance Sheet: company-scoped native submitted accounting with consistent date/currency/permission basis and reconciliation visibility.
- Bank reconciliation: statement import identity, matching suggestions, approvals and retained evidence; avoid duplicate receipts/payments.

## Required failure handling for future writes

- Frappe unavailable: leave operational transaction committed and accounting outbox pending; expose status, avoid silent success; retry with backoff within reviewed limits.
- Network timeout: outcome is unknown. Look up the unique external reference and compare digest/docstatus before any create retry. Do not generate a fresh posting identity.
- Permission denied: fail closed; no retry storm, identity escalation or automatic grant. Require administrator review of explicit action/company permissions.
- Duplicate external reference: fetch the existing document; adopt only if source, company, digest and submitted outcome match. Otherwise quarantine as reconciliation required.
- Validation failure: record a sanitized actionable field/code, retain immutable attempted payload, correct mappings/source through approved revision and resubmit deliberately. Do not retry unchanged invalid data forever.
- Document already submitted: verify identity/digest/result and acknowledge matching completion. Corrections require approved cancellation/amendment/credit; never mutate submitted evidence silently.
- Partial synchronization: retain each native link/outcome. Resume only missing approved steps; compensating accounting actions require approval. Never repost an invoice merely because its payment failed.
- Retried request: same key/digest/actor/scope returns recorded outcome. Changed payload under the same key is a conflict. Concurrent workers use leases and remote uniqueness.
- Reconciliation mismatch: freeze automatic completion/retries for that event, identify source/native differences and seek independent review. No automatic balancing journal or destructive overwrite.

## Read-only status failure behavior now

Missing/unsafe config returns not_configured with no connection. Missing JWT returns 401; missing entitlement/action permission returns 403 with no connection. Native denial, timeout, malformed identity response, redirect and transport/server errors return sanitized diagnostic states with 503. An authenticated identity response returns 200 with available=false. No financial event is queued or written.

## Safe manual diagnostics, not executed here

Run from normal Windows PowerShell only if inspection is authorized. These commands inspect the existing named container and do not initialize, migrate, restart, build or restore anything:

```powershell
Invoke-RestMethod -Uri 'http://finance.localhost:8082/api/method/ping'
docker ps --filter 'label=com.docker.compose.project=navapack-finance'
docker exec navapack-finance-backend-1 bench version
docker exec navapack-finance-backend-1 bench --site finance.localhost list-apps
```

Verify actual container name with docker ps before exec. Ping/version/list-apps do not verify authenticated accounting access or company configuration. Authenticated verification must use private runtime secrets and return only reviewed nonsecret results. Do not paste credentials or full site configs. Any repair, credential creation, migration, resource change or deployment requires separate approval.
