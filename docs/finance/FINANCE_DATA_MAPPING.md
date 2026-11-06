# Finance data mapping and ownership plan

6 November 2026. Everything below is future design; no model, migration, mapping records, outbox or synchronization is implemented by this increment.

## CA-owned prerequisites

The mappings below are future contract candidates, not financial configuration or permission to create masters. Company, fiscal year, Chart of Accounts, currencies, taxes, opening balances, banks, valuation/recognition policies and production financial data are selected and approved by the client’s CA / Finance Manager later. No mapping is assumed, seeded or automatically inferred in the technical increment. accounting_setup=not_assessed is neutral about existing data. Authentication can be tested without any of these masters.

React configuration screens must display unknown/unassessed values without fabricated defaults, sample balances or successful-save simulations. Native field contracts can guide component design; live data and business meaning require approved scope and validation. Financial writes and COA retrieval remain absent.

## Source and remote identity

Use stable keys, never names or visible document numbers as the sole match. Every mapping is scoped to environment, site and company. Do not merge customers, suppliers, variants or units by label. Resolve unknown values explicitly; unknown opening balances must not silently become zero.

- Django Customer primary key → ERPNext Customer name, with approved tax/address/contact mapping and independent active-status handling.
- Django Supplier primary key → ERPNext Supplier name. Legacy free-text supplier identities require manual review.
- FinishedStockItem/raw material/product identity + variant + exact unit → Item name and approved UOM conversion. Manufacturing models/line IDs are not interchangeable with canonical product IDs.
- Existing local account code + accounting purpose + company → ERPNext Account name. Cash/bank, receivable/payable control, sales, expense, stock valuation, WIP, COGS, tax and LED mappings require approval.
- Explicit approved legal entity → Company; reporting/transaction currency, fiscal year, cost center and exchange-rate rules must be verified. No currency/company is inferred from fictional preview INR values or old local examples.

## Operational invoices

Operational invoice source is SharedEntry key `invoices`, with stable invoice id, orderId and line itemId/unit, dispatchIds, date, currency, immutable amounts and approved tax/LED snapshots. Capture the specific EntryRevision version/hash at eligibility; never post a mutable current browser projection.

Existing local accounting Document may already reference `invoice:<invoice-id>` through PostingEvent. This local posting link is evidence to reconcile, not authorization to create an additional ERPNext invoice. Historical documents are not automatically migrated.

Proposed immutable posting identity: environment/site/company + source kind + stable invoice id + approved revision + target DocType + action. Use a persisted UUID or collision-resistant digest representation as the external reference; do not depend on invoice number uniqueness across systems.

Store these future links: source key/id/revision/hash; external reference; remote doctype/name/docstatus; request digest; last reconciled remote digest; requesting/approving actors; posting actor/correlation; timestamps; attempts and sanitized failure code. Enforce one active authoritative submitted invoice per source posting identity and explicit amendment/reversal linkage.

Suggested local states: Not eligible → Awaiting mapping → Awaiting approval → Queued → In progress → Draft remote → Submitted. Side states: Retry pending, Validation failed, Reconciliation required, Cancelled and Superseded. Distinguish operational issuance from ERPNext draft/submitted state. Do not label an invoice posted based only on a transport 200.

ERPNext Sales Invoice mapping uses Customer, company, posting date, currency, approved conversion rate, mapped Item/UOM/account/tax lines, source references and exact approved totals. Preserve line identities and deterministic rounding. Use update_stock=0 because physical stock is owned by confirmed Django dispatch. Posting must not create a second operational invoice or duplicate local journal.

## Other planned documents

- Approved supplier bill/receipt match → Purchase Invoice, with supplier, company, invoice reference, item/account/UOM/tax mapping and receipt evidence. Do not create another receipt or stock movement from bill posting; use update_stock=0 for Django-owned stock.
- Approved issued SalesCreditNote + immutable source invoice/return/QC evidence → ERPNext return Sales Invoice linked to the original native invoice. Financial credit does not imply physical stock receipt. Jurisdictional VAT/LED treatment must be approved before posting.
- Approved receipt/payment with cash/bank account and exact allocations → Payment Entry linked to native invoice names and currencies. Prevent the same cash movement from being represented twice. Retain unallocated/advance balances explicitly.
- Independently approved balanced adjustment → Journal Entry with period, account, company, currency, debit/credit and source evidence. No balancing journal may be invented to hide a reconciliation mismatch.
- Bank statement transaction → native Bank Transaction/reconciliation link, with immutable import identity and independent reconciliation approval. No automatic cash posting based solely on a matching description.

## Reporting

ERPNext submitted documents/GL become the reporting authority only for the approved cutover scope. General Ledger, AR/AP, Trial Balance, P&L and Balance Sheet must carry company, period, currency, basis, permissions and pending/reconciliation warnings. Never sum local historical ledger and ERPNext GL without an approved non-overlapping scope and reconciled opening balances.

Physical quantity reporting remains Django-owned. Financial valuation/reporting follows the future approved accounting policy. Preserve source precision and currency-specific decimal rules; no mixed-unit or mixed-currency aggregate may masquerade as a balance.
