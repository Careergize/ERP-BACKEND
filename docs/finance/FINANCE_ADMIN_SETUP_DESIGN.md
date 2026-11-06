# Finance Administrator setup design

Design only. No user/role grants, configuration records, models, migrations, approval endpoints or activation controls are implemented.

## Ownership and boundaries

The client’s CA / Finance Manager owns company setup, Chart of Accounts, fiscal years, taxes, opening balances, bank accounts, accounting policies and production financial master data. The technical team provides authenticated transport, permission enforcement, validated API contracts and reusable presentation components. Do not seed example production companies/accounts or infer business settings from development fixtures.

Existing Navapack login, sidebar/routes, Django operational authority and current Django accounting remain intact. Future setup screens live within that application; there is no separate Finance login and no browser Frappe credential. The existing Admin role never implies Finance entitlement or setup/approval authority.

## Implemented readiness presentation

FinanceReadinessPanel shows not-checked, checking, not-configured, unavailable/denied/timeout/invalid-response or authenticated technical state. FinanceSetupGuide displays “Awaiting CA configuration and approval” only after a validated authenticated diagnostic result. That phrase describes a future handoff, not an assessment of actual company configuration. The copy explicitly says accounting setup has not been assessed.

Policy metadata always remains accounting_setup=not_assessed, posting_enabled=false, available=false. There are no setup inputs, save/approve buttons, mock-success notifications, sample financial values or activation controls. Diagnostic credentials stay on the server or in the manual script’s transient private process memory.

## Proposed future capabilities

Names below are proposed action capabilities, not existing permissions or role grants:

- View setup: inspect authorized company-scoped status and retained evidence.
- Prepare setup: the authorized CA/Finance Manager prepares a versioned configuration package.
- Submit setup: freeze the exact package for review; preparation permission does not imply submission or approval.
- Independently approve setup: an explicitly authorized reviewer evaluates that frozen revision and supporting evidence. Creators, editors, submitters and other contributors cannot approve their own package, including superusers. If the CA prepares the package, a separately authorized qualified reviewer is required.
- CA sign-off: retain explicit approval of accounting policy/configuration by the client’s designated CA / Finance Manager, bound to the same revision. This does not replace independent approval and does not authorize posting.
- Activate approved configuration: a separately authorized operator may request activation only after all technical, authorization, CA sign-off, independent-review and operational cutover controls pass. Activation must not be an implicit consequence of saving or approving setup.

All capabilities require active Navapack authentication, explicit Finance entitlement and approved company scope. Future real setup permissions must be defined and enforced server-side before functional React controls. Existing grant-view permission authorizes diagnostics only, not these actions.

## Proposed screen sequence

1. Connection and authorization: display sanitized technical evidence, identity verification and company-scope status without exposing credentials.
2. CA setup overview: unassessed sections for company, periods, accounts, taxes, openings, banks and policies. Do not preselect values. Each section will use a separately verified native API contract and explicit action permission.
3. Review package: show the exact revision, changes, evidence, validators and unresolved items; never represent a button click as a durable save without a working backend.
4. Independent review and CA sign-off: record actors, decisions, reasons and timestamps against the immutable package. Changes create a new revision and invalidate prior approvals.
5. Activation review: show explicit blockers and request a separately authorized activation. No production posting until successful approved activation; an unauthenticated/unavailable technical check cannot be treated as accounting failure or approval.

## Evidence and concurrency prerequisites

A future implementation needs durable configuration revisions, contributor provenance, content digest, source-site/company identity, validation evidence, CA sign-off, independent review, idempotent action receipts and immutable activation/revocation events. Reject stale versions, mismatched site/company/revision, replayed changed payloads, self-review and revoked permissions. Existing operational evidence patterns are references, not Finance storage.

Future validation must check live native permissions, installed schema/version and policy consistency without overwriting existing financial records. Missing data remains explicit. Configuration changes after approval require review again; opening balances or journals must never be generated as a technical workaround. Company-scope permissions, approval schema and any required migrations need separate design/approval.

## Native contracts and React mapping, not implemented

- Technical status: existing Django GET /api/finance/status/, backed by authenticated Frappe GET /api/method/frappe.auth.get_logged_user. This is the only implemented Frappe integration request.
- Future account presentation: the pinned ERPNext Account schema defines name/account_number/account_name/company/parent_account/root_type/account_type/report_type/account_currency/is_group/disabled. Preserve native IDs and nulls; convert native check flags to booleans; no mapping onto local ledger codes by name.
- Future fiscal/tax/bank/opening/configuration forms require individual pinned-source and live contract review before coding. No assumptions are made about native endpoints, calculations, company currency or selected accounting policy. No placeholder API routes are registered.

References: [Frappe identity method, v16.55.1](https://github.com/frappe/frappe/blob/v16.55.1/frappe/auth.py), [REST authentication](https://docs.frappe.io/framework/user/en/api/rest), [Account schema, ERPNext v16.53.0](https://github.com/frappe/erpnext/blob/v16.53.0/erpnext/accounts/doctype/account/account.json). Source verification is not live installation or permission verification.

## Required future tests

Test each capability independently, entitlement and company isolation, contributor/self-review rejection including superusers, CA sign-off without independent approval, approval without technical readiness, activation without cutover authorization, stale revisions, replay/conflict handling, revocation, missing/unknown values, secret suppression and denied upstream requests. Retain existing login, navigation, operational/accounting idempotency/audit and dispatch-stock regressions. Use disposable test databases and synthetic fixtures only; no production sample records.
