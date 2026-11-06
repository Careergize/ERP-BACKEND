# Finance API contract

6 November 2026. Implemented local API only; live Frappe transport unverified.

## Existing APIs, unchanged

GET `/api/auth/applications/`: existing JWT authentication. Finance object contains entitled (fresh grant eligibility), available=false, reason=frappe_sso_not_configured. No Frappe request and no token/session handoff. Client must not infer access from role.

POST `/api/auth/finance/launch/`: 401 unauthenticated, 403 not eligible, 503 eligible but integration/SSO unavailable. No redirect, cookie or authorization code. This increment does not call or modify launch.

Existing `/api/auth/login/`, `/api/auth/me/`, refresh/logout and all `/api/accounting/` and operational APIs are unchanged.

## Added API

GET `/api/finance/status/` is a real connection diagnostic, not an accounting/document endpoint.

Authorization: Django JWT + active user + explicit current eligible FinanceEntitlement + accounts.view_financeentitlement. Standard Django direct/group/active assigned-role permission infrastructure applies. Role labels alone are insufficient. Superuser permission behavior does not bypass entitlement. Query parameters and bodies cannot select upstream host, identity, role, company or method.

Response fields are restricted to:

```json
{"available":false,"reason":"frappe_sso_not_configured","connection":"not_configured","read_only":true,"accounting_setup":"not_assessed","posting_enabled":false}
```

The additive accounting_setup field is always `not_assessed`; it does not mean the client has configured, omitted or failed any accounting setup. posting_enabled is always false. Neither field is read from financial masters or client input. No new endpoint or permission was added.

- 200: connection=authenticated; expected integration identity matched. available is still false.
- 503: connection=not_configured, permission_denied, unavailable, timeout or invalid_response. No secret, upstream URL, mapped identity, native body or exception is included.
- 401: missing/invalid/expired JWT or inactive user rejected by existing authentication.
- 403: missing/invalid entitlement or diagnostic permission; no upstream request.
- 405: POST/PUT/PATCH/DELETE for authorized users; no upstream request. Authentication/permission failures may take precedence.
- HEAD follows GET semantics and may perform the same read-only diagnostic. OPTIONS uses DRF metadata after authentication/permissions. No accounting methods exist.
- All gateway responses, including failures, set Cache-Control:no-store and Vary:Authorization,Cookie,Accept. No Frappe cookies or Location are forwarded. Existing framework authentication/throttle errors retain standard DRF bodies; upstream data never enters them.

The framework's existing rate throttling applies. No automatic retry is configured. API requests do not mutate accounting/grant models or shared operational records.

## Upstream contract

Only GET `<server-configured-root>/api/method/frappe.auth.get_logged_user` is called. Header: Authorization:token API_KEY:API_SECRET. No Django JWT or browser-selected identity reaches Frappe. A 200 JSON object must contain message exactly matching FINANCE_FRAPPE_IDENTITY. Guest/Administrator identities are rejected at configuration validation. The integration key must belong to a least-privilege native identity; native permissions/company scope still require live verification.

Server-only configuration names:

- FINANCE_FRAPPE_URL: empty by default, root origin only; HTTPS remotely.
- FINANCE_FRAPPE_IDENTITY: expected nonprivileged native identity; empty by default.
- FINANCE_FRAPPE_API_KEY and FINANCE_FRAPPE_API_SECRET: empty by default; private runtime secret storage only.

Do not place real values in React, public Vercel variables, commits or reports. No production configuration or secret was provisioned. Missing settings return not_configured with no outbound request.

Reference: [Frappe REST authentication and API conventions](https://docs.frappe.io/framework/user/en/api/rest). Documentation availability does not verify this site's endpoint or credentials.

## Frontend behavior

The shared Axios client fetches `/auth/applications/` and validates booleans. Navigation is hidden while loading, denied or errored; entitled users may see a link to the explicitly unavailable status shell. No ledger, account list, financial action or launch control is present. The shell remains unavailable even if a future/misconfigured applications response advertises true.

An explicit status click uses `/finance/status/`; 503 bodies are validated and rendered as fixed application messages, 403 becomes a permission message, and other failures become a generic retry message. Raw backend/upstream messages are never rendered. Status requests abort on unmount. API key/identity is never part of the browser contract.

The React client validates available=false, posting_enabled=false, accounting_setup=not_assessed and read_only=true. An authenticated state is accepted only with HTTP 200; other connection states require 503. Missing/contradictory metadata is an error. The CA setup guide appears only after that authenticated response, and disappears immediately on retry/loading, errors or access-boundary unmount. No check runs automatically.

The manual PowerShell script independently probes fixed ping and identity contracts and outputs site_reachable/identity_verified booleans plus normalized policy flags. It never returns credentials, native identity or native bodies. It does not call Django or confer entitlement.

No Chart of Accounts, invoice/payment/credit/journal posting, report, master sync or reconciliation endpoints were added. Future contracts must be reviewed and implemented with real permissions and transport rather than registered as placeholders.
