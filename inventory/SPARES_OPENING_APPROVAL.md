# Spares opening approval implementation — 28 October 2026

## Scope and status

Implemented on `codex/spares-opening-approval` in both repositories, preserving pre-existing uncommitted catalogue work. No commit, merge, deployment, operational permission grant or shared migration was performed. All Django verification used inert settings, skipped dotenv, blocked outbound sockets and used in-memory or disposable file SQLite databases. Existing Spares masters, codes, imported/local categories and initialized movements remain intact. No accounting entries or correction/adjustment workflow was added.

The original warning was a deliberate safety restriction: ordinary Spares posting had no independently approved opening workflow. Existing `/api/spares/`, capabilities, detail, movements, history/activity and summary APIs, authentication and catalogue transaction/audit services are reused. No alternate inventory ledger or duplicate movement API was introduced.

## Behavior and authorization

Unknown opening/current balances remain null while an opening request is pending or rejected. Explicit zero becomes known only after independent approval. Master creation rejects non-null opening_stock, including zero. Direct opening movement submission is denied even to superusers; only the internal review service supplies the sealed proposal authority. Existing initialized legacy openings are preserved without fabricated retrospective approvals.

New explicit permissions (inventory / SpareOpeningRequest content type): `request_spareopening`, `review_spareopening`, `view_spareopening`. Existing view_spareitem is also required. Existing direct grants, groups and active assigned-role permission sources are reused; role labels and superuser status do not imply these grants. Requesters see their own requests; explicitly authorized reviewers/readers can see all. Proposed requesters: Store Keeper/Admin; proposed reviewers: Production Manager/designated reviewer. These are proposals only. No assignment was made. The requester cannot approve OR reject their own request, including a superuser who has both grants. A database check also prevents equal decision requester/reviewer IDs.

Ordinary IN/OUT retains existing view/add-movement permissions. Approval itself needs explicit review authority, not ordinary movement-add permission. This distinction allows an independently designated reviewer to approve without granting arbitrary stock posting. Permission revocation is rechecked on retries.

Opening proposals have immutable content, server time/user, UUID submission identity and snapshot digest. Decisions carry a separate UUID command identity and immutable evidence. Both reuse existing protected ERP EntryRevision audit records. Approval, existing movement posting, decision, proposal transition and audit commit atomically under the existing catalogue mutex/retry infrastructure. Stock quantity/unit/date/negative-balance checks, server movement references and actor/payload-bound idempotency remain in that service. Competing decisions cannot post twice. Rejection creates no movement. Audit failure rolls everything back. These are application-level protections, not a claim of tamper-proof database certification.

## APIs and migration

Necessary actions added to the existing Spares viewset:

- GET `/api/spares/opening-requests/`: scoped, paginated queue with status/search.
- GET/POST `/api/spares/{item_id}/opening-requests/`: scoped item queue / immutable proposal.
- POST `/api/spares/opening-requests/{request_id}/review/`: approve/reject the exact digest.

Existing capabilities now report separate base-schema and opening-schema readiness, version 1 and explicit opening authority; direct can_open is false. Spares item responses add opening_state. Missing base catalogue tables/seed returns a safe 503; missing only opening tables disables opening actions while preserving master and authorized ordinary posting for initialized stock. No HTTP action migrates or initializes storage.

Additive migration `inventory/0012_spare_opening_approval.py` depends on inventory 0011 and the existing user model. It creates SpareOpeningRequest and SpareOpeningDecision, uniqueness/check constraints and permission definitions through Django's normal mechanism. It assigns no permissions, copies no records and changes no existing item/movement data. Tested backward/forward only on isolated test storage. The shared local database still reports inventory 0010; neither 0011 nor 0012 was applied here. Rollout of the pre-existing 0011 dependency requires its own reviewed plan because it predates this Spares-only correction.

## Frontend

Spares-only request/review panel shows proposal, requester, immutable snapshot hash, decision and movement reference. Stock IN/OUT selects initialized active items and never sends is_opening=true. Pending badges and null/zero displays are explicit. Existing master editor and imported/local filters remain. New error handling distinguishes authentication, permission, migration/schema, transport/API, validation and conflict failures from a genuinely empty result. Confirmed master results survive a failed capabilities request. Unexpected successful HTML/invalid responses do not produce stock/approval success. Existing API URL/auth client is unchanged.

Retry IDs and submitted forms remain fixed while an outcome is ambiguous. IDs are visible but not persisted through a full page reload; after a lost response and reload, inspect requests/movement history before starting another business event. This is request-id idempotency, not automatic detection of two distinct IDs for the same business receipt.

## Exact implementation files for this correction

Backend modified:
- inventory/catalog_api.py — SpareSerializer opening state and SpareViewSet mixin only.
- inventory/catalogue_service.py — Spares-only direct-opening/master guards and private authority parameter; Consumables behavior retained.
- inventory/models.py — imports the two new models.
- inventory/test_catalog.py — existing Spares fixtures now use real independent approval.
- inventory/test_catalogue_integration.py — Spares integration fixture uses independent approval; Consumables tests retained.

Backend created:
- inventory/spare_opening_models.py
- inventory/spare_openings.py
- inventory/spare_opening_api.py
- inventory/spare_opening_test_helpers.py
- inventory/migrations/0012_spare_opening_approval.py
- inventory/test_spare_openings.py
- spares_opening_concurrency_check.py
- inventory/SPARES_OPENING_APPROVAL.md (this report)

Frontend modified:
- src/pages/Registers/ItemRegisterPage.tsx — Spares branches and Spares master/history error handling; existing Consumables path retained.
- docs/DEVELOPMENT_STATUS.md
- docs/BACKEND_TODO.md
- docs/DATA_MODELS.md
- docs/BUSINESS_RULES.md
- docs/CHANGELOG.md

Frontend created:
- src/lib/sparesOpeningApi.ts
- src/pages/Spares/SparesPostingForm.tsx
- src/pages/Spares/SparesOpeningPanel.tsx
- scripts/check-spares-openings.mjs

No dependency, environment, route, sidebar, authentication, shared API-client or other module source change. Hashes captured before implementation confirm registerApi.ts, the three Consumables page/form files and catalog_models.py remain unchanged. Git status also includes pre-existing changes from earlier tasks; those are not newly attributed to this correction. Test evidence files under backend/tmp are retained separately.

## Remaining verification and rollout gates

Browser/mobile interaction and the intended deployed frontend/backend versions were not verified. Configured source default API is https://35.175.179.96/api, with VITE_API_BASE_URL override; no deployment URL was approved or changed. Before deployment, independently verify intended API origin in the actual served bundle, backend revision/capability response, migration status/plan, approved explicit role assignments, and different real requester/reviewer users. PostgreSQL/target-engine execution remains unverified; concurrency was tested with real JWT on disposable file SQLite. No deployment or migration permission is implied by this implementation.

A shared db.sqlite3 hash mismatch was observed during the session (file modification time 14:02:44 local). Our test runners explicitly isolate all databases; no write/migration command targeted that file. The cause is unconfirmed, so byte-for-byte preservation of the shared database cannot be certified. Read-only verification found inventory migration 0010 last, zero Spares items/movements and zero opening permissions. Do not restore or overwrite this file; reconcile its external activity before rollout.

## Executed verification and results

Working and tested in isolation:

- `../backend-test-venv/Scripts/python.exe -B test_baseline_runner.py test inventory.test_spare_openings inventory.test_catalog inventory.test_catalogue_integration`: **40/40 passed**, including 19 new opening/migration tests. Covers approval, self-review including superusers, explicit/revoked grants, ordinary authorized/unauthorized IN/OUT, malformed/forged requests, null versus zero, master/direct bypass, immutable evidence, rollback, replay/conflicts, imported/local filtering, legacy identities and unrelated inventory/accounting separation.
- `../backend-test-venv/Scripts/python.exe -B spares_opening_concurrency_check.py`: **passed** on disposable file SQLite with real JWT and separate connections. Duplicate proposals, competing reviewers, approval/rejection races, decision replay, superuser self-denial, concurrent ordinary overspend/replay, unique references and reload persistence verified.
- `test_baseline_runner.py check`, `drift`, `graph`: **passed**; no model drift or migration graph conflict. Actual forward/backward migration tested in the 40-test run. Shared migrations not applied.
- All **26** `frontend/scripts/check-*.mjs` scripts: **passed**. New Spares check also rerun after final error/response checks; registers and catalogue integration checks rerun after final shared-register Spares-only edits. These are adapter/SSR/data checks, not browser interaction.
- `node node_modules/typescript/bin/tsc -b`: **passed**.
- `npm run build`: **passed**, existing >500 kB chunk warning remains (approximately 1.23 MB main JS). No lint script is configured. Existing SSR useLayoutEffect warnings remain.
- Git diff whitespace checks passed in both repositories.

Broader regression outcome:

- Isolated `test_baseline_runner.py test` (accounts, ERP, inventory, quotations): **249 tests, 243 passed, six pre-existing errors, zero assertion failures**.
- Isolated `test_baseline_runner.py test accounting`: **19/19 passed**.
- Combined final suite coverage: **268 tests, 262 passed, six pre-existing errors**. Before this correction, the same suite had 249 tests and the same six errors. The initial intermediate approval run exposed a migration test fixture missing the existing mutex after TransactionTestCase flush; that test fixture was corrected and both focused and final regression runs pass it.
- Existing errors are all `erp.test_baseline_characterization.WorkflowBaselineTests`, each missing the existing `prepare_quote` helper: test_job_wrong_saved_unit_rejects_issue_without_stock_change; test_legacy_json_orders_and_jobs_read_without_new_control_fields; test_multi_material_failure_rolls_back_earlier_movement_and_job_json; test_quote_lineage_is_preserved_in_entry_and_top_level_order_fields; test_two_manufacturing_lines_and_stock_share_one_snapshot_parent; test_unsafe_current_draft_allocation_and_same_corrector_approval. Unrelated source was not changed to fix them.
- Automated regression coverage executed for authentication/roles, masters/customers, ERP shared entries, orders/fulfillment, job cards/production, finished/raw/recycled inventory and procurement, QC, quotations/invoices/accounting/costing and existing Consumables. This does not certify every browser page or deployed route.

Evidence: backend/tmp/spares-opening-baseline.txt, spares-opening-focused-final.txt, spares-opening-regression-final.txt, spares-opening-accounting-final.txt, spares-opening-concurrency-final.txt, spares-opening-system-check.txt, spares-opening-drift.txt, spares-opening-graph.txt, spares-opening-before.json and spares-opening-frontend-* logs.

Implemented but not fully verified: interactive desktop/mobile UI and target database/deployment behavior.
Pending: separately approved target rollout/migrations, explicit role assignments, actual served API URL/revisions and browser acceptance.
Blocked certification: the unrelated six baseline test errors and unexplained shared database hash change prevent claiming a completely clean full suite or byte-identical shared data. No new test failure remains in executed checks.
