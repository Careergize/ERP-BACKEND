# Release-readiness review — 6 November 2026

Three independent source repositories are prepared. ERP-FRONTEND owns React and
the fictional demo. ERP-BACKEND owns Django accounting/entitlement/gateway.
ERP/Navapack-Finance is the prepared third source root for native app and
infrastructure. The old ERP/finance directory remains untouched. No commits,
pushes, merges, deployments, shared database migrations or runtime Docker actions
were performed. Git branches/remotes were not changed in the real application
repositories; the third root has not been initialized or connected to GitHub.

## Completed validation

- TypeScript/operational frontend build: PASS (existing >500 kB chunk warning).
- Separate static Finance demo build: PASS, about 305 kB JS before gzip.
- Frontend non-browser release suite: 23/23 PASS in an isolated source copy.
- Preview browser: PASS all 22 routes, desktop/mobile/keyboard/ARIA, disabled
  actions, production and flag-off denial and zero API requests.
- Gateway and dispatch fixture browsers: PASS.
- Static demo browser: PASS all 22 routes desktop/mobile, disabled financial
  actions, reload reset, unchanged token, no APIs/external requests.
- Full backend security/operational suite: 386/386 PASS, in-memory DB only.
- Isolated backend Finance/diagnostic suite: 42/42 PASS.
- Repaired baseline fixture suite: 8/8 PASS; no production business logic changed.
- Independent frontend source: fresh npm ci, operational/demo builds and 23 checks
  PASS with no sibling repositories. Offline cache initially missed a package;
  the approved network lockfile install succeeded. No dependencies were added.
- Explicit integration: 559 hash assertions plus diagnostic script parity PASS.
- Infrastructure independent source: 159 upstream file hashes and exact inventory
  PASS; Compose config --quiet PASS without engine operations, including from a
  separate source copy. Its temporary Git init also verified ignore behavior.
- Backend model/migration drift and source/index whitespace checks: PASS.

Raw validation artifacts are local/ignored (ERP/Gap-Analysis/release-* and
backend/tmp/release-*); generated browser evidence is not staged. Initial sandbox
CDP browser failures were resolved by loopback-permitted runs. The static check
was corrected to classify embedded data:image SVGs as local resources, then passed.

## Security and file exclusion

No real credential was detected by the scoped source scan. One AWS-style match
in the byte-identical pinned upstream local S3 pytest fixture is a public EXAMPLE
literal, not an operator credential; do not alter vendored source to hide it.
This scan is not a complete historical secret audit. Git history was not rewritten.

Real env variants, keys, local sites/databases/volumes, backups, caches, logs,
builds and review outputs are ignored and excluded from Docker contexts. Safe
placeholder templates and required source assets/lockfiles remain. Eight frontend
and 145 backend generated files are STAGED FOR INDEX DELETION ONLY; local files
still exist. No source changes were staged. Exact deletion/source inventories are
in release-readiness-inventory.json. Removing index entries does not purge history.

The native app and vendored upstream were copied from Git-tracked source only;
no nested .git, credentials or old runtime files were copied. Cross-repository
baselines record reviewed hash deltas instead of dropping business assertions.
The 35 retired targets were runtime secrets/state/backups/generated recipe and
upstream Git metadata. Original operational/native source assertions remain.

## Git branch and commit plan — approval required

1. Frontend: create codex/finance-release-readiness from the current main, retaining
   the working tree. Review staged generated-file removals; stage ignore rules and
   commit `chore: exclude generated runtime artifacts`. Then review/stage the
   pending Finance UI/demo/tests/docs and commit `feat(finance): prepare protected demo`.
2. Backend: create codex/finance-release-readiness. Review staged output removals
   and ignore rules; use the same cleanup subject. Commit the pending approved
   foundation separately as `feat(finance): add disabled read foundation`, with a
   body noting explicit grants, additive schema and disabled activation. Commit
   repository-owned diagnostic/test fixture/docs repairs separately as
   `test: make finance checks repository independent`.
3. Third source root: after review, initialize Git with main, add the approved
   Careergize/Navapack-Finance remote and create codex/finance-source-release. Stage
   only the exact source inventory; commit `build(finance): pin native infrastructure`.
   Review the source lock, upstream license, native app and public examples.
4. In each repo, inspect git diff --cached --check/name-status/full diff, perform
   the source/secret audit, and record the three reviewed revision IDs. Fetch/rebase
   only after commits and review any conflicts with affected tests. Push feature
   branches without force and open linked PRs; do not merge/deploy automatically.

No bulk add over ERP or the old finance directory. Recheck any newly staged paths;
the existing staged deletions are intentional cleanup, not missing local files.

## Remaining approvals and limitations

- Host-level demo authentication/protection must be configured before deployment
  or sharing. The static artifact/CSP is not authentication. No hosting project or
  authentication service was created or configured here.
- The third GitHub remote/repository has not been verified/created by this batch.
- Native image builds, live ERPNext behavior and hermetic package locking were not
  validated; only pinned source and Compose configuration were checked.
- Gateway gates/provider remain disabled/unavailable. Native permissions, secret
  provider, database-enforced audit immutability/retention and explicit migration,
  financial activation and cutover approvals remain future dependencies.
- Old runtime/history directories and Git history remain preserved locally.
