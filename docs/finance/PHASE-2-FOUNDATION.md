# Phase 2: disabled Django Finance foundation

6 November 2026. This batch implements integration metadata and access discovery,
not a live accounting connection. No shared database migrations, credentials,
native configuration, Docker changes, deployment or cutover are authorized here.

## Delivered boundary

Existing Navapack JWT, FinanceEntitlement eligibility, application availability,
launch refusal and `/api/finance/status/` diagnostic behavior are unchanged.
Django accounting posting and operational code are unchanged. Preview is unchanged.

New settings are literal safe defaults, independently of the legacy diagnostic:

- `FINANCE_GATEWAY_ACCESS_ENABLED = False`
- `FINANCE_GATEWAY_READS_ENABLED = False`
- `FINANCE_GATEWAY_DESTINATIONS = {}`

There is no new outbound HTTP transport and no financial resource endpoint.
No posting, synchronization, activation, financial writes or ERPNext browser
session implementation exists in the new foundation.

## Approved schema and migration review

`backend/finance_gateway/migrations/0001_initial.py` creates exactly six models:
FinanceSite, FinanceCompanyReference, FinanceIdentityBinding, FinanceCompanyGrant,
FinanceCapabilityGrant and FinanceAuditEvent. Metadata UUIDs/timestamps, disabled
defaults, protected references, optional expiry and opaque company references
follow the approved schema. References describe already-existing native objects;
they do not create accounting companies or other native records.

Uniqueness covers site/company, user/site binding, case-insensitive native
username/site, user/company grant, and grant/capability. Capability values have
both Django choices and a database CHECK constraint. Native identity constraints
reject empty, Administrator and Guest values; runtime validation also rejects
reserved identities and requires an exact match to the eligible entitlement's
trimmed native username. Normal model saves call full_clean.

The migration depends on the configured user model and existing
`accounts.0004_financeentitlement`. It has no RunPython/RunSQL data operations,
seeds, changes to existing accounting tables or credential storage. Django's
ordinary permission/content-type metadata is not an explicit Finance grant.
Do not register unrestricted grant-management APIs or automatically assign grants.

Migration generation and validation use `backend/finance_foundation_runner.py`.
It skips dotenv, clears the process environment while evaluating actual settings,
uses only in-memory SQLite, and blocks outbound socket connections. The migration
mode applies migrations in memory, verifies all six tables are empty, reverses
only finance_gateway, checks removal of its tables and reapplies the migration.
Other applications' existing seed migrations run only in that disposable memory
database. No shared development/staging/production database is opened by this runner.

## Authorization and credentials

Every protected authorization evaluation queries current entitlement, active
company/site, unexpired company/capability grants and an enabled/unexpired native
identity binding. There is no role, staff, superuser or Django has_perm bypass.
Access discovery is never a reusable authorization token.

The server-only CredentialResolver protocol returns an expiring native credential
envelope. The only shipped provider is UnavailableResolver. No environment-secret
fallback, shared service identity, secret-store adapter or provisioning exists.
Resolution rejects missing/empty/revoked/expired credentials, wrong site/user and
unsafe token characters. Credential repr omits key and secret. Native permission
and enabled-user verification have NOT been performed; a future reviewed transport
must verify those before returning financial records.

Company-scoped access additionally requires `finance.access.read`. Other allowed
values are `finance.setup.read`, `finance.accounts.read`, `finance.parties.read`
and `finance.reports.read`. The future-read authorization primitive rechecks all
conditions and requires the separate read gate. It is not an audited read
executor and must not be exposed directly as a financial endpoint.

## Fixed access contract

`GET /api/finance/v1/access/` uses the existing JWT authentication classes.

- Anonymous/invalid/expired JWT: 401.
- Missing/ineligible entitlement: 403.
- Eligible user with disabled access gate: sanitized 503.
- Enabled gate with no effective companies: 200 with `companies: []`.
- Enabled gate with eligible grants/bindings: validate independent destination
  policy and resolve credentials before returning company-scoped capabilities.
- Missing secrets or rejected destinations: sanitized 503; no partial result.
- Database/read errors: sanitized 503.
- Mandatory audit persistence failure: 503, including otherwise successful reads.

Successful responses contain `companies` entries with only `reference`,
`display_name`, `capabilities`; `available`, `posting_enabled`,
`activation_enabled`, `synchronization_enabled` are always false and `read_only`
is always true. Native IDs, usernames, site URLs and secret references are omitted.
Request company/user parameters cannot enlarge scope. Unknown and inaccessible
company references receive the same denial from the authorization primitive.
HEAD follows GET authorization; OPTIONS exposes framework metadata only.
Responses use no-store and a server-generated correlation header.

## Destination and network security

The independently configured destination mapping keys an exact base URL to an
exact native site name and explicit approved IP addresses. Database site records
cannot approve themselves. Validation rejects credentials, paths, query strings,
fragments, invalid ports, unsupported schemes, unapproved destinations, empty DNS
results and any unexpected resolved address. Production permits public HTTPS
destinations only. Explicit local-development entries additionally require DEBUG;
link-local, multicast and unspecified addresses are never accepted.

No new HTTP connection can occur in this batch. Future transport MUST connect to
the validated IP set without another DNS lookup, preserve original hostname TLS
verification/SNI, disable redirects and proxy/netrc inheritance, apply response
and timing bounds, and verify native identity/permissions. Ordinary requests.get
after DNS validation is insufficient against rebinding. Network egress enforcement
and bounded DNS resolution remain deployment requirements. The preserved legacy
diagnostic has its own older transport/policy; it is not permission to reuse that
credential or transport for financial reads.

## Audit and production limitations

Access discovery audits success, denials and failures, with actor, correlation,
action, sanitized reason/outcome/status and elapsed time. An access-discovery
event is request-wide, with no native identifiers or arbitrary payload fields.
Invalid JWT is audited without an authenticated actor. Inaccessible company
references are not persisted as verified company links.

Audit model saves are insert-only. Instance/queryset deletion, queryset update,
bulk update and bulk create are blocked; default/base manager use these controls.
These application protections are NOT database immutability. Before production:

- Apply database privileges/triggers preventing audit UPDATE/DELETE by the runtime
  account, including raw SQL and alternative manager/queryset paths.
- Separate migration/maintenance credentials from runtime credentials.
- Approve retention, legal holds, controlled purge/archive and access policy.
- Provide a durable independent audit sink, monitoring, backup/restore and access
  review. Audit storage failure intentionally makes new gateway reads unavailable.
- Validate constraints/migrations and concurrency on the actual deployment DB;
  this batch's executable migration evidence is SQLite only.

CA-created company/accounts/taxes/fiscal years/openings, approved currency and
accounting policy, native identity/role/user permissions, a reviewed secret-store
adapter, fixed native read adapters, report contracts and explicit environment
approval remain prerequisites. No VAT, LED, currency or accounting policy is
inferred from demo fixtures or existing legacy defaults.

## Validation and rollback

Commands run from backend, all through the isolated runner:

```
python -B finance_foundation_runner.py generate
python -B finance_foundation_runner.py migration
python -B finance_foundation_runner.py drift
python -B finance_foundation_runner.py test finance_gateway.test_foundation
python -B finance_foundation_runner.py test finance_gateway.tests accounts accounting erp inventory quotations
```

Evidence is in backend/tmp/finance-foundation-security.txt,
finance-foundation-migration.txt, finance-foundation-regression.txt and
finance-foundation-baseline-investigation.txt. Completed outcomes:

- Foundation security suite: 25/25 PASS, including expiry/revocation, company
  isolation, capability enforcement at both layers, superuser/staff denial,
  binding/credential mismatch, missing/revoked credentials, gates, DNS/allowlist,
  audit failures, error sanitization and absence of financial side effects.
- Migration apply/reverse/reapply and empty Finance table assertions: PASS.
- Migration/model drift check and Django system checks: PASS.
- Existing gateway/accounts/accounting/ERP/inventory/quotations suite: 361 tests,
  355 pass and 6 errors in 366.642 seconds. No failures outside the unchanged
  WorkflowBaselineTests fixture errors below. Existing diagnostic tests pass.
- Existing operational baseline investigation: 8 tests, 2 pass and 6 errors.
  All six error stacks identify the unchanged WorkflowBaselineTests fixture
  missing prepare_quote when reusing ProductionWorkflowTests.create_order.
  Existing operational tests were not edited to hide or bypass these errors.

## Exact batch file inventory

Modified existing files (preserving pre-existing work):

- `backend/config/settings.py`: register the app and add disabled/empty settings.
- `backend/finance_gateway/urls.py`: add only v1/access; retain status.

New implementation and validation files:

- `backend/finance_gateway/apps.py`
- `backend/finance_gateway/models.py`
- `backend/finance_gateway/destinations.py`
- `backend/finance_gateway/credentials.py`
- `backend/finance_gateway/authorization.py`
- `backend/finance_gateway/access.py`
- `backend/finance_gateway/migrations/__init__.py`
- `backend/finance_gateway/migrations/0001_initial.py`
- `backend/finance_gateway/test_foundation.py`
- `backend/finance_foundation_runner.py`
- `finance/docs/PHASE-2-FOUNDATION.md`

Generated evidence files:

- `backend/tmp/finance-foundation-security.txt`
- `backend/tmp/finance-foundation-migration.txt`
- `backend/tmp/finance-foundation-regression.txt`
- `backend/tmp/finance-foundation-baseline-investigation.txt`

Other dirty/untracked files already existed before this batch and are not part of
this inventory. No native app, Docker, frontend, accounting or operational source
was changed.

Rollback: keep both new gates false and restore the previous application build.
Do not delete audit evidence or reverse a shared database migration merely to
roll back code. No existing accounting route or posting hook requires rollback
because none was modified. Migration reversal was tested only on empty in-memory
tables; populated PROTECT relationships require a separate reviewed procedure.
