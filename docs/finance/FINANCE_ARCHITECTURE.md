# Finance architecture

6 November 2026. This document separates the implemented read-only foundation from future approved accounting ownership.

## Request path

React → existing Django JWT API → Finance permission boundary → Finance adapter → fixed Frappe authenticated GET endpoint → ERPNext/Frappe.

The browser uses existing Navapack login, AuthContext, Axios session and AppShell. It never authenticates to Frappe, receives integration secrets, imports the preview or opens a Frappe login. `/finance/erpnext` is a status shell; `/finance` remains Django accounting.

FinanceAccessProvider requests the existing applications endpoint for a signed-in token-bearing account, aborts stale requests on account changes and rechecks on window focus or retry. Missing/malformed access, network errors and demo-only sessions hide the new navigation. Explicit entitlement permits viewing the unavailable shell; it does not unlock accounting. QC users retain their existing route rules with an exception for this new route, which is independently entitlement guarded.

Status is fetched only on an explicit diagnostic button click. Django enforces a valid active JWT user, finance_eligible and the existing accounts.view_financeentitlement permission. Existing direct/group/active-assigned-role permission mechanisms are reused. Admin or superuser without entitlement is denied. No new permission or migration is needed. The default Django model permission was created by existing auth/content-type migration infrastructure; no grant is seeded here.

## Availability and authentication

The existing applications availability flag remains false and launch remains blocked. A 200 diagnostic response means the configured integration identity answered the fixed authenticated endpoint, not that a Navapack user has native Finance access. Every diagnostic response still says available=false. There is no ready/enable switch.

The adapter uses a server-only integration API key/secret and expected identity. It permits HTTP only for explicit development DEBUG and localhost/127.0.0.1/finance.localhost. Remote configurations require HTTPS. It rejects embedded credentials, URL paths/query/fragment, invalid ports, privileged/blank identities and header newline injection; it disables redirects, inherited proxy/.netrc credentials and automatic retries. Connect/read timeouts are 3/5 seconds; body consumption is limited to 16 KiB. Only normalized states leave the adapter; upstream bodies, exceptions, keys and identity are not returned or logged by gateway code.

An integration identity must later be created and reviewed outside this increment with minimal read-only permissions and company restrictions. An authenticated identity response cannot establish those permissions. Do not use Administrator or Guest. Do not expose credentials as VITE variables or public Vercel settings. No browser-side privileged authentication or JWT-to-Frappe token assumption is permitted.

## Technical readiness and CA ownership

The status response separates integration connectivity from accounting assessment: accounting_setup is always not_assessed, posting_enabled is always false and available remains false. An authenticated integration identity does not prove accounting configuration, native data permissions, CA sign-off or production readiness. No accounting-master queries are needed for technical authentication.

React reuses FinanceReadinessPanel and FinanceSetupGuide inside the existing Finance route. The guide is rendered only following an authenticated HTTP 200 with validated disabled/not-assessed flags. It contains information only; no approval/configuration controls, invented financial records or assumed company/currency values. A new status check clears the old result before awaiting transport.

The client’s CA / Finance Manager owns all company/accounting configuration decisions, including fiscal year, accounts, taxes, opening balances, banks and accounting policies. Technical implementers must not prepopulate these. Existing local accounting remains operational and unchanged. Any eventual ERPNext configuration and posting work needs an explicitly approved scope and CA-led decisions.

Finance Administrator is a future explicit capability, not the existing Admin role. Preparation, submission, independent review, CA sign-off and activation must be separated. Every configuration revision invalidates approvals; contributors cannot independently approve their own revision, including superusers. Technical checks, valid scope-bound approval and separately authorized activation must all pass before any future posting enablement. No activation switch exists now. See FINANCE_ADMIN_SETUP_DESIGN.md.

## Data ownership

Django remains authoritative for user authentication, explicit entitlement, operational/manufacturing records, Sales Orders, confirmed dispatch, physical inventory, approvals and operational evidence.

Target ERPNext ownership, after approved cutover: Chart of Accounts, accounting journals, posted sales/purchase invoices, payment entries, General Ledger, receivables/payables, trial balance and financial statements. React is presentation only.

Current Django accounting is already an active posting implementation. During this increment it remains unchanged and authoritative for its existing records/workflows. No ERPNext financial documents are created, no balances are combined and no new accounting projection claims authority. ERPNext is not yet the posting authority for current Navapack transactions.

Before enabling writes, approve a company-specific cutover date, source scope, opening balances, mappings and ownership flag that routes each new accounting event to one authoritative posting engine. Existing local hooks must not continue authoritative posting for a scope simultaneously sent to ERPNext. Preserve historical Django evidence as read-only history after any approved cutover. A future control must reject dual-destination posting, rather than rely on staff selecting the correct UI. No such cutover control is implemented here.

## Operational boundary

Sales Orders express demand and create no physical deduction. Confirmed dispatch remains the stock reduction authority. Future ERPNext Sales Invoices must use update_stock=0 for Django-owned operational stock. Returns/credit notes must not add duplicate stock; receipt/disposition remains in the operational workflow. Accounting valuation and COGS recognition require approved mappings and policy; never infer physical movements from a financial posting retry.

VAT/LED calculations and tax accounts require confirmed client policy and immutable approved source snapshots. Do not derive new rules from preview fixtures, assume a jurisdiction or hardcode rates. Existing local calculations remain untouched.

## Future controls, documentation only

- Durable transactional outbox in Django, committed atomically with an eligible operational decision. A worker posts outside the operational transaction; no synchronous network call blocks dispatch commits.
- Stable source/revision/company/document-type key, immutable payload digest, unique remote external reference, worker lease and attempt history. ERPNext-native duplicate enforcement must back application checks.
- Approved source event plus separate posting permission and maker/checker controls. Financial edits, submission, cancellation and refunds require action-level authorization, period controls and retained evidence.
- Store remote name/docstatus/result digest, source actor, approving actor, worker correlation ID, attempts and reconciliation outcomes. Attribute actual ERPNext writes to the integration identity while retaining the requesting/approving Navapack actors explicitly.
- Reconciliation jobs compare source snapshots, remote submitted documents, allocations and amounts. Mismatches become review items and never automatic balancing journals.
- Development/staging/production use separate hosts, credentials, companies, queues and identifiers. Remote environments require HTTPS. Production activation, secrets provisioning, database migrations, service changes and deployment require separate approval.

Diagnostics have no financial side effect and add no financial audit records. Existing accounting audit history is preserved. A complete durable financial audit trail is a prerequisite for future writes, not a claim about this status-only endpoint.
