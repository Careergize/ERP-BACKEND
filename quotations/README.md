# Quotation PDF and delivery

## Quotation-first orders and Sales Order approval — 23 October 2026

Current flow: saved Quotation → Sales Order awaiting approval → Production Manager
or Admin approval → automatic manufacturing Job Card and planned costing sheet.
This replaces the earlier order-first automatic quotation/allocation behavior.
The existing quotation review and customer-response UI still precedes conversion.
Manual selling prices may be entered without a preliminary costing estimate;
linked estimates retain their approval/model checks. Job Card costing is reviewed
after creation, before production approval and Store material issue.

New orders must identify an exact saved quotation revision and retain its lines,
prices, units, customer, tax/discount and LED snapshot. Direct order creation is
rejected by the API. Order approval is persisted with actor/time, limited to
Production Manager/Admin, atomic with job creation and safe to retry. Stock-only
orders create no Job Card. Existing-item manufacture still requires known zero
stock, rechecked at approval. Standalone manual production Job Cards remain allowed.

Sales Orders offers “Start with quotation”; the old new-order route opens Quotations.
Stock OUT new-sale entry saves a quotation first. Dispatch against an existing
order requires Sales Order approval. Finished-goods dispatch remains browser-session
state; this change does not add database persistence to dispatch or customer responses.
Migration erp.0015 grants the Sales Order approval permission to PM/Admin roles.


## Previous order-origin quotations (superseded) — 23 October 2026

Creating a direct `/api/production-workflow/orders/` order also creates one editable
QuotationDraft atomically. Orders return generatedQuotationId/generatedQuotationNumber;
the draft retains salesOrderId and exact saved commercial lines. Same order retries
return the existing document; quotation-origin conversions do not create a second one.
Optional quotationTemplate contains company and validityDays; absent configuration
uses existing Navapack identity/banks and the supplied template's commercial defaults.
Review before sending: no implicit approval, signature, delivery or stock action.
LED input/snapshots survive draft restore, and public PDF totals retain LED/VAT.
Migration 0006 grants Receptionist/Admin quotation view/add. Thirty-seven combined
workflow/quotation tests pass on SQLite, including totals, rollback and retry behavior.
The existing PDF template was rendered and visually verified. No remote deployment
or external sending was performed; older orders are not backfilled.

## Persistent editable drafts — 16 October 2026

Real-user **Save Draft** now POSTs `/api/quotation-drafts/` and waits for database confirmation. Django Admin → Quotations → Quotation drafts displays the saved records; they are read-only in admin so editing follows the API's version/freeze rules. The existing Quotation model remains the immutable PDF/manager decision snapshot. Creating a blank editor is not itself a database save; click Save Draft. Demo login still stores drafts only in memory and is labeled accordingly.

`GET /api/quotation-drafts/` paginates the current user's records; GET detail loads one. View permission is `quotations.view_quotation`; saving requires `quotations.add_quotation`. POST accepts `{ "editor_data": {...}, "expected_version": 0 }`. Editor data carries the client UUID id/rootId/revision, document fields, contacts/company/terms, salesRepresentative and line IDs/source references/entered quantities/prices/units. An empty customer/zero lines can be saved as an incomplete draft. Entered lines still require valid positive quantities, nonnegative prices and active unit identity. Caller status, approval and audit fields are not adopted. No stock/job/order side effect occurs.

First save creates version 1; later saves use the same UUID and current expected_version. Exact retry returns the saved version without duplication. Changed stale versions return 409. Other users cannot read or overwrite the draft. Draft metadata does not enter customer PDFs or manager public projections. If any immutable snapshot for the draft UUID has been submitted, changing that draft returns 409; use Edit as New Revision to get a fresh UUID. New revisions preserve the root reference. Loading combines saved editor metadata with server snapshot decisions and derives historical supersession from saved revision numbers. Rejected manager reasons and approved/sent state remain authoritative from the snapshot service.

The React list/provider reloads saved drafts on real login and supports explicit Refresh Saved Quotations; detail/editor routes wait for that load. Product/model/costing IDs are retained, but their other backing frontend stores are still separate and some are session-only. Customer responses, conversion events and full business lifecycle audits are not made durable by this change. Older immutable PDF snapshots without editor metadata are not automatically treated as editable drafts, and browser-only drafts lost before this feature cannot be recovered from Django. Older documentation below saying the editor is session-only is superseded for the validated draft fields described here.


## Project Manager roles, review and rejection — 16 October 2026

Superusers open frontend **Masters → Users**, create a username/password and assign **Project Manager**. Existing users can be reassigned with the Role selector there, or in Django Admin → Accounts → Users → Assigned role. Django Admin → Accounts → Roles manages the supported role records and their permissions. Role codes are the supported application role types; their names/permissions/active flags are configurable. Migrations create defaults and preserve existing users' roles. Project Manager and the existing Production Manager role both receive quotation view/approval permissions; manager users are normal users without staff/superuser access.

`GET /api/roles/` returns active role records for superusers. `/api/users/` lists/creates real users and `/api/users/{id}/` supports PATCH, all superuser-only. Creation requires a validated initial password and active assigned_role ID. Passwords are write-only and hashed; client is_staff/is_superuser flags are not writable. Existing Django direct/group permissions are preserved; the role permission backend additionally reads the assigned role permission set. Role reassignment appears after profile restoration/login. Inactive role assignments grant no role permissions or manager authority.

Managers sign in, open **Quotations → Manager Approval Inbox → View Quotation**, inspect customer/line/price/VAT/terms data, then Approve or enter a rejection reason and Reject. **All submitted quotations** shows approved/rejected decisions as well as pending submissions. The old pending list remains `?pending=true`; `?submitted=true` includes all decisions. POST `/api/quotations/{id}/reject/` accepts `{ "reason": "Please revise prices" }`, records rejected_by/rejected_at/rejection_reason, and requires manager role plus approve_quotation permission. Responses include rejection state and reviewed_by. Same-reason retry returns the original decision; different reasons and later opposite decisions return 409. Rejected snapshots cannot be sent, approved or resubmitted. A newly revised snapshot must be submitted/reviewed. Creator delivery screens show the rejection reason after checking manager status. No email/WhatsApp message is sent by a review action.

The Project Manager role is now explicit; older text below mentioning Production Manager alone is superseded. The full commercial editor is still session-only, and rejection of a saved snapshot does not automatically rewrite its local frontend lifecycle. The server decision remains authoritative for review and delivery. No real user/password was created or changed during implementation; user creation/review tests use isolated test databases.


## Units and line amounts — 16 October 2026

Manage units through Django Admin → ERP → Units or frontend Masters → Units. The migration seeds kg, ctn, pcs, roll, pack, ltr and m. `/api/units/` lists/creates and `/api/units/{id}/` reads/updates; `?active=true` filters active units. Standard view/add/change unit permissions apply. Codes are unique ignoring case and cannot be renamed through the API; deactivate obsolete units instead of deleting them. API codes normalize to lowercase. No quantity conversions are defined.

Quotation lines accept an optional `unit_id` plus the existing `unit` code. The backend resolves an active master, rejects mismatches/unknown/inactive units, and snapshots the master ID/code. Legacy clients can still supply the code alone. Existing stored documents retain their original unit text; Product.unit remains a legacy text field. The frontend dropdown loads from the backend rather than a hardcoded list, and stock variants retain their existing declared unit. Demo login has no real unit-master access.

`lineTotal` = quantity × entered unitPrice, rounded half-up to two decimals; client totals are ignored. `unitPriceInclVat` is the entered price for inclusive VAT or price × (1 + taxRate/100), represented to six decimals for exclusive VAT. The original unit price drives line multiplication, and VAT is included or added exactly once at document totals. Unit prices preserve up to six decimal places. The PDF prints the VAT basis beside Unit Price and labels the line total Qty x Price. New units/content require approval on their new saved snapshot as before.


## Required manager approval — 16 October 2026

The manager in this workflow is the existing `production_manager` account role. Sales creates/edits a quotation and clicks **Submit to Manager**. Real login persists the exact public PDF snapshot and POSTs `/api/quotations/{id}/submit/`. Managers open **Quotations → Manager Approval Inbox**, review the PDF and approve it. Only `production_manager` users with `quotations.approve_quotation` permission may approve; Admin/Accounts and even a superuser with a different role cannot bypass this rule. Managers also need `quotations.view_quotation` for their inbox/review. Sales accounts need quotation add/view/send permissions for their own records. No user permissions are granted automatically by this change.

The sender opens **PDF / Email / WhatsApp**, clicks **Check Manager Approval / History**, selects the intended channels and clicks **Send Selected**. Approval does not send automatically. Backend delivery requires a submission timestamp and approval by a manager, even if a client forges status/approval fields. GET `/api/quotations/?pending=true` lists submitted unapproved snapshots visible to the user. Old generic PDF approvals are insufficient: submit the snapshot and obtain manager approval again. New content creates a new unapproved snapshot; historical approved documents remain immutable. Full quotation-family supersession/revision enforcement is still separate from this snapshot service.

Run migrations after updating. Email/WhatsApp credentials remain required. Demo-mode approvals never authorize real delivery. The previous generic approval wording below is superseded by this manager-only rule. The manager inbox and delivery approval live in Django; the existing complete commercial editor/lifecycle remains session-only.

## Saved customer master — 16 October 2026

Quotation **Add New Customer** uses the shared Django customer model. Name, contact person, phone, email, billing/delivery addresses, TIN and B2B/B2C type are saved by POST `/api/customers/`. Customer codes are generated if omitted; a write-only UUID `creation_key` makes a retry reuse the same customer, with conflicting details rejected. Real sessions reload all active customer pages; database IDs are represented in the frontend as `db:{id}`. New customers are selected on the quotation and appear in Masters → Customers; existing customer edits there use PATCH. A saved quotation snapshot may link to `customer_master`, while its historical contact text remains unchanged by later master edits. Backend view/add/change permissions apply. Demo login cannot persist new customers and reports that explicitly.


The A4 PDF follows the supplied 15 October 2026 photograph: Navapack branding, company/customer blocks, phone/email, item grid, VAT/total and words, bank/signatory panels and terms. The company/bank defaults were transcribed from the photo and remain editable in frontend company settings; review them before commercial use. No signature was copied. `sample.json` is a layout example with the four reference items, not a real customer send instruction. The photo's customer email was absent, so the example leaves it empty. The supplied +91 6282744675 is the editable WhatsApp recipient default; it is not the sending Business account.

## Use in the frontend

Log in with a real backend user (disable frontend mock login), create/review the quotation and open its detail page. Click **PDF / Email / WhatsApp**. **Download PDF** validates and saves the public quotation snapshot to Django, then downloads the native PDF. After commercial approval, an authorized backend approver clicks **Approve PDF for Sending** to approve that exact immutable snapshot. Choose Email and/or WhatsApp, check the recipients, then click **Send Selected**. Both options start unchecked. No provider call occurs without a selected channel. Opening, approving and downloading never send a message.

Email attaches the generated PDF; WhatsApp uploads the same PDF to Meta and references it in an approved template. The separate WhatsApp recipient defaults to +916282744675 and can be changed. The customer phone/email are included on the quotation itself from the editor's contact snapshot. Changing the delivery email only changes the envelope recipient, not the PDF's contact snapshot.

Frontend quotation editing/lifecycle remains session-only. PDF snapshots, server approval and delivery attempts persist in Django independently. This feature does not implement the complete proposed quotation family/revision, source-costing validation, customer acceptance or sales-order conversion backend. Server PDF approval is an explicit review of imported public fields; it does not claim to verify frontend-only costing approvals. API clients cannot assert approval by supplying a status or totals. Historical saved snapshots are available through the list/detail APIs and admin; the existing frontend list is not yet backed by these snapshots.

## API contracts

All endpoints require JWT and explicit Django permissions. Assign permissions through admin groups; display roles alone grant no access. An approver can see all snapshots; other users can only see their own. `view_quotation`, `add_quotation`, `approve_quotation`, and `send_quotation` govern the corresponding operations. For extraction, also require `erp.view_salesorder`.

| Endpoint | Behavior |
| --- | --- |
| `POST /api/quotations/extract/` | Read existing sales order/customer/lines and return validated template fields and computed totals; does not save/send |
| `POST /api/quotations/` | Validate public template fields, calculate decimal totals and persist immutable snapshot; exact repeat returns same snapshot |
| `GET /api/quotations/` | Paginated visible saved snapshots |
| `GET /api/quotations/{uuid}/` | Extract saved public fields, computed totals, server approval and delivery history |
| `GET /api/quotations/{uuid}/pdf/` | Generate attachment PDF; unapproved snapshots bear DRAFT label |
| `POST /api/quotations/{uuid}/approve/` | Approve exact current, unexpired PDF snapshot; actor/time recorded |
| `POST /api/quotations/{uuid}/send/` | Send only selected channels, with durable attempt records |

Create body shape is demonstrated by `sample.json`. Extra frontend/private fields are discarded through nested field whitelists. `source_id` identifies the frontend record; number/revision are public supplied references, not an authoritative server sequence. Server recomputes validity and totals, rounds line amounts and monetary totals to two decimal places using decimal half-up rounding, preserves six-decimal entered unit price/quantity precision, and validates positive quantities, nonnegative prices, tax range and discount. It never creates jobs, orders or stock movements. Snapshot data cannot be edited or deleted via these endpoints; changing commercial content creates a new snapshot requiring fresh approval.

Extraction body example (use `company` and `terms` objects in `sample.json`):

```json
{
  "sales_order_id": 1,
  "company": {"tradingName": "NAVAPACK", "legalName": "Nova Recycling Industries Ltd", "address": "Kampala", "telephone": "0753349228", "email": "sales@example.com", "tin": "1017362107", "signatory": "", "banks": []},
  "terms": {"priceValidity": "14 days", "deliveryPeriod": "14 days", "paymentTerms": "30 days credit", "revisionClause": "", "additionalTerms": "", "remarks": ""},
  "currency": "UGX", "taxRate": "18", "taxMode": "inclusive", "validityDays": 14
}
```

Supply the tax basis/currency explicitly: the starter sales-order model does not store them. Extraction reads structured ERP data; arbitrary scanned-image OCR is not implemented.

Send body:

```json
{
  "request_id": "79814a1d-74e5-447d-9c24-76d0d2332143",
  "channels": ["email", "whatsapp"],
  "email": "customer@example.com",
  "phone": "+916282744675"
}
```

Email is required only for selected Email; a country-code phone is required only for selected WhatsApp. Missing configuration returns 503 before either channel is attempted. Validation errors return 400, missing permissions 403, conflicting reuse of a send request ID 409. Individual results use `accepted`, `failed`, `unknown` or `pending`. Accepted means SMTP/Meta accepted the submission, not recipient delivery/read confirmation. No delivery webhook is implemented. A timeout or disconnect may mean the provider already accepted the document: unknown/pending requests are never automatically retried. Check provider logs first. An exact request replay returns saved results without submitting again; reuse with different channels/recipients fails. The UI retains the request ID after interrupted sends; **Prepare New Send Request** explicitly creates a new operation. Select only failed channels for intentional retries, to avoid repeating a successful submission.

## Configure real sending

Add to private `Backend/.env` (the placeholders below are not working credentials):

```dotenv
EMAIL_HOST=smtp.your-provider.example
EMAIL_PORT=587
EMAIL_HOST_USER=your-smtp-user
EMAIL_HOST_PASSWORD=your-app-password
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=sales@your-domain.example

WHATSAPP_ACCESS_TOKEN=your-private-meta-token
WHATSAPP_PHONE_NUMBER_ID=your-business-phone-number-id
WHATSAPP_GRAPH_VERSION=your-supported-graph-version
WHATSAPP_TEMPLATE_NAME=your-approved-quotation-template
WHATSAPP_TEMPLATE_LANGUAGE=en
```

Use the Graph API version supported by your Meta app. Register an approved WhatsApp Business template with a **DOCUMENT header** and **one body text variable** for the quotation number, e.g. `Please find your quotation {{1}} attached.` Template name/language must match the approved template exactly. Sender credentials are separate from the recipient's phone number. Test-account recipients must be permitted in Meta. The application submits an approved template so it does not assume an active customer service conversation. Restart Django after changing `.env`. Do not place SMTP/Meta tokens in Vite variables or commit `.env`.

Requests run synchronously and record attempts before contacting providers. Production scale needs a background worker/outbox, shared throttling, deployment-specific monitoring and reconciliation of pending attempts. No real email or WhatsApp message was sent during implementation/testing; transports were mocked, and email attachment content was checked with Django's in-memory backend.

Run `python manage.py migrate` after installing updated `requirements.txt`; run `python manage.py test` from `Backend` to validate.
