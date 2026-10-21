# Raw inventory and procurement persistence

## Manual procurement tracking — 21 October 2026

The planner is a manual tracking and forecasting tool. `stock_in_store` and
`as_of_date` retain the quantity/date entered by the user. Daily/monthly consumption
never reduces this balance automatically. API `stock` and `currentStock` both return
the saved quantity, including zero/null. No schema migration is needed for this change.

Daily consumption = monthly consumption / consumption days. Estimated coverage is
entered stock / daily consumption; estimated availability and order-by dates start
from the saved stock date. These forecasts do not post inventory movements. Changing
a rate or opening the editor does not change stock or reset its date. Users manually
update the balance and stock date when recording a new count.

New plans prefill from the selected raw material's current stock for review; saved
stock remains independent, including unknown and zero balances. Expected deliveries
only extend forecast coverage. Neither planner saves nor forecast dates change
Raw Material or finished-goods Stock. Actual receipts/issues use inventory workflows.
Existing saved quantities and revision history are preserved without recalculation.

The React frontend connects to this active adjacent Backend application through JWT.
The separate monorepo Phase 1 backend is unchanged. Reference import/migration history
is retained: raw grades, exact units, unknown openings and original movements are not
reseeded on reads or refreshes.

Verification: Django checks/migration drift, 16 inventory tests, frontend build and
procurement checks pass on local SQLite; PostgreSQL concurrency is not exercised.

## Database models

- `RawMaterial`: exact material/grade name, family, category, unit, nullable opening stock,
  opening date and minimum stock.
- `RawMovement`: each Stock IN receipt or consumption OUT, quantity/date, reference,
  remarks, request ID and server-recorded creator/time.
- `ProcurementPlan`: monthly consumption override, consumption days, lead time, comments,
  current version and last editor/time, linked to the material.
- `ProcurementDelivery`: each expected supplier delivery as its own row, linked to its
  plan, with exact arrival date, decimal quantity and creator/editor timestamps.
- `ProcurementPlanRevision`: a full snapshot for each saved plan version, including its
  deliveries, with editor/time. Identical retries create no extra revision. Later edits
  and removal of a delivery do not erase earlier saved snapshots.

Procurement stock is manually entered. Daily/monthly consumption, coverage and
estimated availability/order-by dates are planning calculations from the saved inputs. Expected deliveries never count as
physical receipts until Stock IN is recorded.

Migrations 0004–0006 move previously saved delivery JSON into dedicated delivery rows
and preserve the current saved plan as its first history entry. Earlier overwritten
versions cannot be reconstructed. A tested reverse migration restores delivery JSON.
All five models are available for read-only inspection in Django Admin; changes use the
existing validated application APIs so stock/version checks cannot be bypassed there.

Authenticated endpoints:

- `GET /api/raw-inventory/`: materials, immutable receipt/consumption ledger, accessible
  saved plans and server-derived write capabilities. Stock is opening + IN - OUT.
- `POST /api/raw-inventory/movements/`: UUID `id`, exact `materialId`, `unit`, `date`,
  `movementType` IN/OUT, positive `quantity`, `reference`, `remarks`. Exact retries return
  the existing entry; changed payloads under the same UUID return 409. No update/delete
  endpoint exists. User identity and timestamp come from the server.
- `PUT /api/procurement-plans/{materialId}/`: `expected_version`, `monthly`, `days`,
  `lead`, `comment`, `deliveries` (UUID id, supplier, exact date, positive quantity).
  Version 0 creates a plan; stale changed saves return 409. Identical retries are safe.

This is one shared store, not separate inventories per user. Read/write permissions use
the existing server permission backend. Migration 0003 grants raw-inventory reads to
existing roles; Store Keeper/Admin may post movements; the planner roles
Admin/Store Keeper/Production Manager/Project Manager/Accounts may save plans. Merely
sending a role string never grants a permission. Superusers retain Django permissions.

Posting validates dates between opening and today and exact units, and locks the grade
inside a transaction. OUT cannot exceed the known physical balance; unknown openings
must be confirmed before issues can post. The source's unknown/negative historical
balances are not rewritten. This feature does not add an opening-adjustment endpoint.
Expected deliveries affect forecasts only; an actual receipt requires Stock IN.

Frontend writes await database confirmation. Failed loads show an error/retry instead
of falling back to sample data. Failed saves keep their form and show the error.
Fresh sign-in/refresh loads shared records; Reload fetches other users' latest entries.
Demo sign-in remains explicitly session-only and makes no inventory API writes.

Verification: Django checks and migration drift pass. The full backend test suite
has previously passed; current focused verification covers all 16 inventory tests covering fresh-session reload, request
retries/conflicts, permissions, quantity/date validation, unknown opening, plan versions,
dedicated delivery rows, retained revision history, cross-material delivery IDs and
forward/reverse migration of existing saved deliveries. Frontend procurement/inventory
regressions also pass. Local verification uses the configured SQLite development
database; PostgreSQL concurrency has not been exercised. Backend QC decision persistence
remains separate from this physical inventory and procurement scope.
