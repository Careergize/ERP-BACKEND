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
