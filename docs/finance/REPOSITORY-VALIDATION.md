# Independent backend validation

The Finance gateway, entitlement and six integration models belong to ERP-BACKEND.
Native Frappe/Docker infrastructure belongs to Careergize/Navapack-Finance.
Backend tests no longer read an adjacent Finance checkout. The read-only private
diagnostic script is owned at scripts/Test-FinanceConnectivity.ps1; cross-repository
integration checks enforce its parity with the infrastructure copy explicitly.

```
python -B finance_foundation_runner.py test finance_gateway accounts accounting erp inventory quotations
python -B finance_foundation_runner.py drift
```

The runner skips dotenv and clears environment while loading inert settings. It
uses only disposable in-memory SQLite and blocks outbound socket connections.
Test migrations run only there, never on a shared development/staging/production
database. No real credentials/grants/native masters are provisioned.

The six historical WorkflowBaselineTests errors were test-fixture defects:
missing prepare_quote/approve_order helpers, an old Draft assertion after the
shared helper had already approved the order, and fabricated quotation identities
overwritten by the current verified quotation fixture. The fixture now checks
Draft/no-job BEFORE approval and verifies saved real quotation lineage at both
entryRequest and top level. Same-corrector job characterization remains explicitly
tagged unsafe, not endorsed as future independent review. No production workflow
logic or authorization was changed to make tests pass. All eight baseline tests pass.

New gateway gates remain false and the credential provider remains unavailable.
The status diagnostic contract, posting ownership, accounting and operational
source remain unchanged. Database-level audit immutability, retention, reviewed
secret provider, native permissions, pinned transport and separate deployment/
migration approvals remain prerequisites documented in PHASE-2-FOUNDATION.md.

Raw logs, database files, backups, generated PDFs/screenshots and local runtime
state are excluded. Existing generated outputs are staged for index removal,
with their local files retained. This does not purge prior Git history.
