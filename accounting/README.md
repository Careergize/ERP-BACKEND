# Finance and accounting — 27 October 2026

The active adjacent Backend now contains an accounting app. The frontend opens it
at `/finance`; Accounts also sees it as the home workspace. This is implemented
source, not a statement that the remote server has been deployed or reconciled.

## Implemented

- A configured single-company book, reporting currency, cutover date, fiscal start
  month and moving weighted-average inventory valuation.
- Chart of accounts, separate cash/bank/mobile-money accounts, expense categories
  and customer/supplier accounts. Canonical ERP parties can be linked by stable ID;
  names are not automatically merged.
- Normalized financial documents/lines, debit/credit journal lines, receipts,
  supplier payments, expense vouchers, internal transfers and opening journals.
- Partial payment allocations to one or several invoices/bills and optionally
  specific purchased item lines. Advances can be applied later without recording
  cash a second time. Bounds, party, currency and date checks prevent overpayment
  allocation. Unallocated amounts remain explicit advances.
- Supplier invoices matched to saved purchase-order lines with original item
  prices, partial billing and overbilling checks. Stock is not received again.
  Net PO prices plus explicitly entered recoverable tax are supported.
- Explicit foreign exchange snapshots, base-currency journals and realized
  settlement exchange differences. Currency totals are never added as though
  they were the same money.
- Customer/supplier and account ledgers with range-opening and running balances;
  payment-to-bill/item traces; date/month/fiscal-year report selection.
- P&L, trial balance, balance sheet with accumulated earnings, direct cash-flow
  categories, cash/bank history, sales/purchase/item detail, expenses by category
  and inventory valuation. Reports/export screens expose posted records only.
- Journal-preserving reversals, original-cost issue returns and restricted receipt
  reversals; forward-only accounting period closure by Admin.
- Persistent automatic-posting events with a visible queue for missing accounting
  inputs. Source event keys and command UUIDs prevent duplicate posting.

## Automatic integration

After book setup, existing operational entry paths enqueue financial events in
their database transaction:

| Source | Accounting effect |
| --- | --- |
| Accepted saved sales invoice | Customer receivable, sales, VAT and configured LED liability |
| Immediate finished-goods purchase receipt / GRN | Inventory value and goods-received clearing for accepted quantities |
| Confirmed dispatch | Weighted-average COGS and inventory value; physical stock is not deducted again |
| Raw-material IN | Valuation receipt queue; missing actual cost must be supplied |
| Raw-material OUT / approved job issue | Weighted-average raw-stock value to WIP |
| Finance receipt/payment/expense | Cash/bank and party/expense/tax accounts, with allocations |

Tax rates are not invented. Existing invoice commercial totals remain authoritative;
the integration apportions discounts/net inclusive amounts to lines and separates
VAT/LED. Admin must map an LED liability account before LED invoices post. New
financial bills/expenses accept explicit recoverable purchase tax amounts; actual
nonrecoverable tax treatment needs explicit cost classification.

Existing supplier POs identify suppliers by text, so creating the actual supplier
bill requires selection of a supplier account. A PO alone is not a supplier invoice
and is not booked as a payable merely when ordered. Raw receipts do not carry
purchase prices in their source model; those prices cannot be reconstructed.

Operational saves are not discarded because a valuation or FX input is missing.
The source and error are retained in Posting Queue. New source documents are posted
only once inputs are sufficient. Reports show a queue warning while events remain
unposted. Changing an operational event's source quantities/prices from the queue
is prohibited. Queue retries may supply missing cost, required FX or a missing
customer mapping. The event retains its source actor; a resolved financial posting
records the authenticated accountant who supplied the inputs.

The explicit **Capture existing operational records since cutover** command
captures saved eligible invoices, confirmed dispatches, immediate receipts, GRNs
and raw movements. Historical records are not silently posted by a schema migration.
Review source completeness and cutover totals before trusting the statements.

## Endpoints

All routes start with `/api/accounting/` and require authenticated active accounts.

- `GET/POST setup/`: bootstrap or initial Admin setup.
- `POST masters/parties/`, `masters/accounts/`, `import-parties/`.
- `POST commands/`: `invoice`, `bill`, `receipt`, `payment`, `allocate`, `expense`,
  `transfer`, `opening`, `stock-receipt`, `stock-issue`, `reverse`.
- `GET reports/?start=YYYY-MM-DD&end=YYYY-MM-DD&party=ID&account=CODE`.
- `GET/POST purchase-bills/`: PO sources / controlled partial supplier billing.
- `GET/POST posting-queue/`; `POST capture-sources/`.
- `POST close-period/`; `POST tax-mapping/` for the LED account.

Admin and Accounts can post. Production/Project Managers have read access. Admin
alone configures accounts/tax mapping and closes periods. Inactive identities/roles
are rejected. These are the implementation defaults; a client-specific approval
matrix was not supplied. No payment approval or external bank transfer is implied.

Posting locks the singleton book before reading balances, then updates documents,
journals, allocations and valuation inside one database transaction. Decimal inputs,
positive one-sided journal constraints and balance checks are enforced. Evidence
querysets reject updates/deletes and journal/allocation/value rows reject instance
edits. Database grants/backups remain operational responsibilities.

## Deployment and cutover

1. Back up the actual server database and deploy Backend source including accounting.
2. From the backend environment run `python manage.py migrate` and
   `python manage.py check`, then restart the existing application service.
3. Deploy the frontend build. Keep the established remote `VITE_API_BASE_URL`.
4. As Admin open Finance & Accounting and configure the company, reporting currency,
   reconciled start date and fiscal-year start month.
5. Link canonical parties, create specific bank/mobile-money and expense accounts,
   and map the applicable LED liability account.
6. Post a reconciled opening trial balance with party-level receivables/payables.
   Inventory opening quantity/value is entered as a valued receipt against opening
   equity, using `raw:<id>` / `finished:<id>` and the exact physical stock unit.
   Do this before later valued movements; backdated valuation is deliberately blocked.
7. Capture eligible operational sources; supply missing costs/FX and match supplier
   bills. Review the queue, party/control balances, inventory quantities/values,
   trial balance and opening cash/bank reconciliation before operational reliance.

No real database migration, source import, opening balance, external payment,
commit, push or deployment was performed during local implementation.

## Limits requiring further workflow/configuration

This release is not full Tally feature parity or statutory certification. It does
not infer a country's tax rules, withholding, fixed-asset depreciation, payroll,
bank-statement reconciliation or an unprovided approval hierarchy. Supplier bill
matching supports recoverable input tax; landed-cost/nonrecoverable tax allocation
and fully matched foreign-currency GRNI differences need dedicated rules.

Raw issue values post to WIP, but automated actual production completion costing
(labour, power, overhead and batch-specific WIP to finished goods) is not wired to
the existing job lifecycle. Valued receipts against WIP are available explicitly.
Recycling bag entries remain operational registers; they are not implicitly sold
or posted as accepted granules stock. A saved uninvoiced dispatch cancellation
automatically restores its original valued cost on the saved reversal date. If it
was never financially posted, its pending cost event is cancelled instead. A
financial invoice reversal remains a separate action; cancellation alone is not
treated as a customer credit note.

Invoice/bill corrections currently use complete reversals and corrected documents;
partial commercial credit notes and supplier return matching are not a separate
document workflow. Allocation/document selectors use the selected report range.
Party opening balances are ledger lines, not imported open-invoice ageing records.
Multi-company consolidation, FIFO and period reopening are not implemented.

Reports currently read the full requested source range without pagination; large
deployments require pagination/summary query optimization. Browser financial report
CSV downloads are provided; formatted accounting PDF/XLSX packs are not implemented.

## Validation

19 focused accounting tests and 21 purchase/recycling regressions passed on a
disposable in-memory SQLite database, with dotenv and outbound network disabled.
46 additional GRN/shared-entry/operational-record/raw-inventory regressions passed.
Tests cover calculations, balance reconciliation, partial allocations, advances,
FX/reversals, as-of reports, permissions, source capture, partial source billing,
idempotency, history preservation and moving-average valuation. Django system and
migration drift checks passed. Frontend TypeScript/production build, shared-entry,
register, LED/finance and built CSS checks passed. All nine accounting sections
rendered with API-shaped fixtures in the dedicated SSR screen check.

PostgreSQL concurrent posting/locking and real browser interaction remain unverified.
Do not interpret SQLite serialization tests as PostgreSQL concurrency verification.

## Finance completion and interface refresh — 30 October 2026

The frontend now opens a redesigned dashboard with scoped neutral navigation, emerald posting actions, responsive metrics/tables, and dedicated Customer profit and Uninvoiced orders screens. Day book vouchers expose their actual journal accounts (including named bank/mobile-money accounts), debit/credit sides, original cost-assignment references and account-ledger links.

`GET /api/accounting/reports/` adds `customerProfit` (customer rows, dated journal audit trail, unassigned income/costs and company reconciliation), `assignableCosts`, and `orderCommitments`. New invoice income is customer-tagged; legacy invoice income uses its canonical document party. New dispatch COGS links the canonical order customer automatically. Explicit `customer` on expense/bill expense commands and stock issues assigns actual expense journals. Supplier bill expense attribution must use expense accounts; stock/asset/clearing purchases are not treated as customer cost merely when purchased.

`POST /api/accounting/commands/` accepts `kind: assign-cost`, `sourceLine`, `customer`, reporting-currency `amount`, `number`, `date`, `notes` and UUID `commandId`. This creates balanced debit/credit entries on the same expense account, shifting part of an unassigned cost to a customer with no change to company expense, cash or party control balances. Amounts cannot exceed the remaining source expense; source/customer/date/currency and closed-period checks apply. Assignment dates follow previous assignments/reversals for that source. Reverse assignments to correct them; reverse all assignments on or before reversing the source. Immutable `CostAssignment` evidence links each assignment to its original journal line. Apply **accounting migration 0005_costassignment** before serving the new reports.

Customer profitability is posted income less explicitly attributed actual expenses, in reporting currency; receipts are not income. All customer profits plus unassigned net reconcile to company P&L. Unassigned expenses and pending events remain visible, so incomplete costing is not represented as fully allocated net profit. Assignments affect their posting period. Historical journals are not rewritten or automatically backfilled.

Uninvoiced orders use current saved operational order scope less recorded sales invoices or posted purchase bills dated through the selected end date. Partial billing and dated bill reversals are supported. Sales invoices waiting for GL inputs still count as operationally invoiced, avoiding duplicate billing demand. Quantities remain separate by line/unit; monetary summaries stay separate by currency. Line commitment values are before discounts, tax and levies; they are not expected cash receipts or GL balances. Current order cancellations/status are respected; historical order revisions are not reconstructed. Incomplete source references appear as report issues.

Validation: 26 accounting API tests plus 24 operational/shared-entry regressions on disposable SQLite; Django check and migration drift checks; frontend TypeScript/production build, twelve-section SSR, LED/finance and CSS checks; Chromium desktop/mobile layout/navigation and synthetic cost-assignment submission. The browser test starts a local fixture-only API, never the configured remote API. `scripts/check-finance-browser.mjs` accepts `CHROME_PATH` and optional `FINANCE_SCREENSHOT`; it uses a disposable profile. Browser screenshots use synthetic data. PostgreSQL concurrent locking, deployed-server migration and live client-data reconciliation remain unverified. No deployment or live book changes were performed.

## Accounting masters — 30 October 2026

Finance → Masters supports creating groups/subgroups, general/cash/bank ledgers, customer/supplier ledgers, voucher-level cost categories, currency definitions and custom voucher types. Existing ledgers can be placed in or removed from a compatible group. Classification must match the parent/ledger: customer groups are assets and supplier groups liabilities. Group changes affect classification in all report periods; journals and balances are not rewritten. Group names are case-insensitively unique. Group trees are create-only; no rename/delete/reparent endpoints are introduced.

Admin manages chart/configuration masters and membership, matching the existing chart-account authority. Accounts can create customer/supplier ledgers. Read permissions remain unchanged. The existing `POST masters/<kind>/` endpoint now accepts `groups`, `accounts`, `parties`, `group-membership`, `cost-categories`, `currencies`, and `voucher-types`. The bootstrap returns groups, group IDs on accounts/parties, costCategories, currencies and voucherTypes.

A custom voucher type has a name and supported baseKind: receipt, payment, invoice (Sales), bill (Purchase), expense, transfer (Contra), or journal. `commands/` accepts optional numeric `voucherType` and `costCategory` references. Mismatched voucher behavior or unknown references are rejected. Cost category is a voucher-level reporting dimension separate from the expense ledger. Reversals retain the original category. Manual Journal commands accept balanced reporting-currency debit/credit lines; receivable/payable controls require the correct party and direct inventory entries are prohibited. Manual journals do not automatically allocate/settle invoices or bills.

Currency masters have a unique uppercase three-letter code, name and optional symbol. They provide voucher choices and do not set exchange rates, change the company reporting currency or change two-decimal posting precision. Existing integrations retain their three-letter-code contract; the voucher selector also preserves currencies already used by historical documents. Selecting a foreign currency clears the rate for explicit entry. Opening, Contra and Journal forms use reporting currency.

Reports add Group Summary (general-ledger descendants), Party Group Summary (customer/supplier descendants, kept separate to avoid control-account duplication), and Cost Category Summary. Parent totals include descendants and must not be added to child totals. Voucher detail/registers and transaction/ledger exports expose custom voucher names/categories.

Apply **0006_finance_masters** after 0005. It creates master tables/nullable references, seeds the five accounting classifications and seven standard voucher types, classifies existing general ledgers by their unchanged account kind, and registers existing book/document currency codes. Existing documents retain null custom-type/category references; no financial amounts or party balances are changed.

Validation: 31 accounting tests plus 24 operational/shared-entry regressions pass on disposable SQLite (55 total). Frontend production build/typecheck, thirteen-section SSR, CSS and LED/finance checks pass. Chromium fixture checks create each master, place a ledger into a group, select the created currency/category/type and submit a matching voucher. Tests cover duplicates, incompatible groups/types, Admin/Accounts authority, Journal balancing/inventory/control guards, category reversals and group rollups. Real PostgreSQL concurrency, migration deployment and live user-data reconciliation remain unverified.

### Accounting schema readiness — 30 October 2026

All accounting API views now verify the installed accounting migration leaf after authentication/role checks. Missing accounting migrations return JSON HTTP 503 with the server-side `python manage.py migrate accounting` action instead of querying absent finance tables. This does not auto-migrate databases or mask unrelated runtime failures. Finance UI errors preserve JSON validation details, identify the request path and HTTP status, and do not display raw HTML pages/query-string secrets.

Read-only diagnosis found all accounting migrations pending in this workspace's configured local SQLite database. The frontend .env targets the separate remote API, so this finding does not establish that remote server's migration state. No local/remote schema migration or deployment was performed. Verified missing-schema response on the configured local DB without writes; 32 accounting tests, frontend build and SSR/error-format checks pass; Django check/drift pass.


## Finance customers in Sales — 30 October 2026

Finance → Masters → Ledgers can create a linked ERP customer alongside a new customer ledger (requires customer creation permission). Existing finance-only customer ledgers can link to a selected active ERP customer by ID, or create one. Links preserve the ledger and journal history; conflicting existing links are rejected. Existing ERP customer selection requires customer view permission. Creation retries use a UUID to avoid duplicate customers.

Sales’ customer filter now includes active ERP customers without invoices and offers Refresh customers. Sales Orders and quotation customer choices should be reloaded after linking. Invoice projections normalize `db:` customer IDs to the same stable filter key.

Validation: 35 accounting tests on disposable SQLite, Django check/migration drift, frontend TypeScript/production build and Finance/Sales scripts pass. PostgreSQL concurrency and deployed-server behavior remain unverified. No deployment or operational database changes were performed.


## Ledger editing and deletion — 31 October 2026

Finance → Masters → Ledgers has an Admin-only editor for ledger name and compatible group. Ledger code/ID, accounting classification, cash/control behavior and ERP source link cannot be edited here. A linked ledger rename changes its accounting label only; it does not rename the ERP customer/supplier. Name/group changes affect report presentation across periods without rewriting journal rows. Requests include the previously read name/group and reject stale edits.

Admin can delete unused custom general ledgers and unlinked customer/supplier ledgers after confirmation. Protected financial references block deletion even for zero balances. Built-in/control accounts, the configured LED account and ERP-linked parties cannot be deleted. An unresolved posting queue blocks deletion because queued payloads may reference a ledger. No automatic deletion of customer masters, transactions or balances is provided.

Existing POST masters routes accept ledger-update and ledger-delete; both enforce Admin authority. No migration is required. Validation: 38 accounting tests on disposable SQLite, Django checks and migration drift, frontend TypeScript/production build and thirteen-section Finance rendering check. PostgreSQL concurrency, browser interaction and deployed-server behavior remain unverified. No deployment or operational database changes performed.


## Approved-order cash/credit dispatch — 31 October 2026

Sales Management → Stock OUT → Existing Sales Order preserves quotation and Sales Order approval. Admin/receptionist profiles (the existing intersection of dispatch and invoice authority) see the order customer's current posted receivables balance in reporting currency and choose Cash (paid in full now) or Credit. Customer selection stays on the quotation/order; its stable ERP ID determines the linked accounting party. Matching names never determine posting. Store dispatch authority remains unchanged and does not grant access to customer financial balances or invoice/receipt posting.

GET /api/accounting/dispatch-options/?orderId=... returns the approved customer's ledger name/ID, posted balance as of today, reporting currency, cash accounts and a company-wide pending-posting count. An unmapped or inactive ERP customer blocks settlement. An unlinked active ERP customer gets its exact canonical accounting link during successful settlement. Balance display is read-only and excludes queued/unposted financial entries; positive means due from customer and negative means customer credit.

The dispatch sale snapshot carries optional payment details (cash/credit, explicit FX, cash account and invoice header/billing fields). The existing versioned inventory save atomically validates stock, builds an immutable operational invoice using server-saved order rates/quantities/taxes/discounts, posts it to the exact customer ledger, and for Cash posts a fully allocated receipt. Credit adds the invoice to receivables; Cash settles this invoice without automatically consuming earlier credits or paying earlier invoices. Foreign sales require an explicit reporting-currency exchange rate. Stock quantity reduces once for both. Amounts and final partial-dispatch discount/tax rounding are calculated server-side. Generated invoice/receipt numbers use stable sale UUIDs; retries do not create duplicates. The invoice is also retained in shared invoice history, preventing separate manual billing of the same dispatch.

Invalid payment accounts, closed periods or financial posting failures roll back the dispatch, invoice, receipt and evidence together. Existing cost-valuation posting can still await cost inputs in Posting Queue; this does not duplicate physical stock. An invoiced dispatch retains the existing credit-note requirement before reversal. This change adds no direct sales, price overrides, partial cash payments or new role grants. No database migration is required.

Validation: 69 accounting/operational/shared-entry tests on disposable SQLite, including seven settlement tests; Django checks and migration drift; frontend TypeScript/build, fulfillment checks and Chromium fixture checks for balance display, locked rates, cash/mobile-money and credit submissions, invoice refresh and balance-read failure. PostgreSQL concurrency and deployed-server behavior remain unverified. No deployment or live client-data writes were performed.


## Quotation customer ledger choices — 31 October 2026

Quotation Customer / prospect now refreshes saved ERP customers on editor mount and has Refresh Customers & Ledgers. Linked ledger customers use the existing ERP customer list. Finance-only customer ledgers appear separately by ledger ID. Admin/Accounts users with ERP customer creation permission can explicitly Link ledger & select customer, preserving the original accounting party/history and attaching its new canonical customer ID to the quotation. Other customer-view users can see ledger identity and request linking from Finance. No name matching or automatic linking on read. Saving is blocked until a selected finance-only ledger is linked or replaced with a saved customer/prospect.

GET customers/ledger-options/ requires ERP customer view permission and returns unlinked customer ledger IDs/names and a canLink flag, never balances. The link operation reuses the existing accounting customer-link authority and UUID retry behavior. Inactive linked customers remain excluded by the active ERP customer query. Ledger choices load independently: a ledger-read failure does not remove saved customer options. No new database model/migration or role grants.

Validation: 47 accounting tests including ledger-choice permissions, explicit linking, same-name separation and existing settlement tests; Django check/drift; frontend TypeScript/build and customer/quotation/persistence scripts pass. Browser interaction, PostgreSQL concurrency and deployment remain unverified for this change. No live client records changed.


## Automatic valuation units and case compatibility — 31 October 2026

Finance → Valuation selects a saved finished-good/raw-material item (or an existing legacy valuation identity), shows its exact ID, automatically fills a read-only unit, and displays the latest valued movement date. GET accounting/valuation-items/ uses existing Finance read authority and exposes catalog unit versus stored valuation unit. Server valuation checks enforce known catalog units. Unit comparisons ignore surrounding whitespace and capitalization only (Ctn/ctn); they do not convert or equate different units. Existing StockValue.unit labels, journals and quantities are not rewritten. Genuine mismatches require reconciliation. Date validation now reports its own message and latest date, separately from unit mismatch.

Validation: 51 accounting tests on disposable SQLite, including queued Ctn/ctn dispatch, repeat-post protection, wrong-unit rejection, unchanged physical stock, historical-unit preservation and separate date errors; Django checks/drift; frontend TypeScript/build and Finance SSR; Chromium synthetic desktop/mobile Finance checks including automatic read-only ctn/kg and posting the selected unit. No migration needed. PostgreSQL concurrency and live deployment remain unverified. No operational balances or historical records changed.


## Currency creation display fields — 31 October 2026

Finance → Masters → Currencies follows the supplied field reference: Symbol, Formal name, ISO Currency Code, Number of decimal places, Show amount in millions, Suffix symbol to amount, Add space between amount and symbol, Word representing amount after decimal, and No. of decimal places for amount in words. The form previews numeric/words output and the register displays saved settings. Display and words precision are independent integers 0–6. Flags require booleans; decimal word is optional, up to 100 characters. Admin-only creation authority and duplicate ISO-code rejection remain.

Currency stores decimal_places, show_in_millions, suffix_symbol, space_between, decimal_word and words_decimal_places. Apply accounting migration 0007_currency_display_fields before serving the updated Finance APIs. Existing currencies get two decimals, prefix/no-space/no-millions and blank decimal-word defaults. No financial documents, balances, FX rates or reporting currency are rewritten. Preferences currently affect the master preview/register only; existing reports, PDFs and posting calculations retain their existing behavior/two-decimal precision. Changing report presentation is separate scope.

Validation: 53 accounting tests on disposable SQLite, including roundtrip/defaults/invalid types/ranges/authority and unchanged journals/book currency; Django checks and migration drift; frontend TypeScript/build, Finance SSR, currency-display helper checks and synthetic Chromium Finance checks covering all creation inputs and saved settings. Migration was tested on disposable databases only; deployment, live migration and PostgreSQL concurrency remain unverified.


## Detailed company/customer Profit & Loss — 31 October 2026

Finance → Reports → Profit & Loss now offers Overall company or an exact customer ledger ID, uses the selected date range, and presents side-by-side trading/gross-profit and operating/net-profit sections. Groups expand into ledgers and their posted voucher/date/customer/cost-category amounts. Detailed CSV includes sections, account totals, voucher rows, warnings and supporting stock/purchase schedules. Mobile columns stack and inner voucher tables scroll.

GET accounting/detailed-profit-loss/ computes period income, cost of goods sold, direct expenses, indirect income/expenses, unclassified amounts, gross profit and net profit from posted JournalLine evidence, including dated reversals and cost assignments. Gross/net and section/group totals use Decimal on the server. Customer attribution matches the existing customer-profit rules (expense line parties; income line parties with document-customer fallback), preserving unassigned reclassification credits. Customer scope excludes company costs without customer attribution and exposes company-wide unassigned totals rather than allocating them implicitly. Same-name customer ledgers remain separate by ID. VAT/receivable opening balances are not P&L income. Foreign entries use posted reporting-currency values, not current FX.

Cash/credit sales breakdown uses only recorded dispatchPayment metadata tied to source invoices; older/unspecified sales remain explicitly unclassified. It is never inferred from current outstanding balances. Pending company posting events in the period warn that missing accounting entries are excluded. Classification/group/name changes affect presentation across all selected periods; journals remain unchanged.

The company report includes supporting posted stock opening/receipts/issues/closing schedules (by exact item and unit) and net posted purchase bills/reversals at saved FX. Perpetual-inventory COGS is already expensed, so these schedules are not added to profit again. No company stock or supplier bill balances are represented as customer-owned stock. Only posted valuation evidence is shown; unknown physical opening costs are not invented.

Apply accounting migration 0008_account_pnl_section after 0007. It adds optional Account.pnl_section metadata without rewriting financial amounts. Admin can classify income/expense accounts via Masters → Ledgers → Profit & Loss ledger classification. Standard 4100 sales and 5100 cost-of-goods-sold keep their fixed purpose; 4200 defaults to indirect income; other unclassified accounts remain visible with a gross-margin warning. Updates require an expected current classification and reject incompatible account kinds/stale updates. No new financial posting or role grants.

Validation: 58 accounting tests on disposable SQLite, including company/customer reconciliation to existing reports, FX/tax exclusion, group/voucher detail, opening/closing stock, non-double-counted purchases/COGS, reversals, cash/credit/unknown sale types, exact customer identity, partial cost assignment and classification authority; Django checks and migration drift; frontend TypeScript/build and Finance SSR. Synthetic Chromium checks cover company/customer P&L, voucher expansion, mobile overflow, classification save and existing Finance regressions. Synthetic layout screenshot visually inspected. Live migration/deployment, client-data reconciliation and PostgreSQL concurrency remain unverified. No live records changed.
