# Stage 5 implementation package

This document accompanies the Stage 5 overlay. It describes the added API contract and the exact files included in the package.

## API

All routes use the existing `/api/v1` prefix and the existing authenticated principal.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/discount-policies` | ADMIN creates a new policy version |
| `GET` | `/discount-policies` | ADMIN lists organization policies |
| `POST` | `/discount-policies/{policy_id}/deactivate` | ADMIN deactivates an active policy; it cannot be reactivated |
| `PUT` | `/purchase-requests/{req_id}/discount` | Request owner sets request discount |
| `GET` | `/purchase-requests/{req_id}/discount` | Reads request discount and applier |
| `PUT` | `/purchase-request-items/{item_id}/discount` | Sets item discount by fixed amount or percentage |
| `GET` | `/purchase-request-items/{item_id}/discount` | Reads item discount and applier |
| `GET` | `/purchase-requests/{req_id}/commercial-snapshot` | Reads current clean Snapshot and its items; returns `null` when no clean Snapshot exists |
| `POST` | `/purchase-requests/{req_id}/discount-approvals` | Builds/reuses the current Snapshot and requests one Approval for the request |
| `GET` | `/purchase-requests/{req_id}/discount-approvals` | Lists Approval records for the request |
| `GET` | `/discount-approvals/{approval_id}/history` | Reads append-only decision history |
| `POST` | `/discount-approvals/{approval_id}/approve` | ADMIN approves; self-approval is forbidden |
| `POST` | `/discount-approvals/{approval_id}/reject` | ADMIN rejects with `{"reason":"..."}` |

### Request discount body

```json
{"amount":"125000"}
```

Send exactly one of `amount` or `percentage`, both as strings. Amount is a whole-Rial non-negative value; percentage is from 0 to 100. Request-level percentage is applied to the remaining eligible amount after item discounts. An item-level percentage is applied to that item’s gross amount. A request-level discount cannot exceed the remaining eligible amount after item discounts.

### Policy body

```json
{
  "scope_type":"ORGANIZATION",
  "scope_id":null,
  "role_code":"SALES",
  "direct_limit_amount":"500000",
  "direct_limit_percent":"2.5",
  "approver_limit_amount":"5000000",
  "approver_limit_percent":"10"
}
```

`scope_type` is `ORGANIZATION` or `PRODUCT`. For organization scope, `scope_id` must be `null`; for product scope it must be a product UUID in the same organization. A new version is created after the previous active version for that scope/role is deactivated. All limits are configuration data, not constants in Core.

### Approval response lifecycle

The Approval state is one of `DRAFT`, `APPROVED`, `REJECTED`, or `INVALIDATED`. The API returns the Approval identifier, request/Snapshot identifiers, commercial version, state, and decision metadata. Rejection reason is mandatory.

## Database objects

Migration `007_stage5.sql` adds exactly eight tables:

1. `discount_policies`
2. `request_commercial_state`
3. `commercial_snapshots`
4. `commercial_snapshot_items`
5. `discount_approvals`
6. `discount_approval_history`
7. `pre_invoice_commercial_links`
8. `discount_application_events`

It also adds the request/item discount-applier columns, dirty-state and immutability triggers, the controlled `SUBMITTED → DRAFT` guard, and the Pre-Invoice issue gate.

## Applying the overlay

Extract the ZIP at the repository root and allow its files to merge into the existing tree. Replace only paths present in the overlay. It is an overlay, not a full source checkout. The package deliberately does not include protected migrations or `runner.py`.

After applying, the migration runner discovers `007_stage5.sql` by its existing numeric filename convention. The API/core PostgreSQL test helpers and migration-order test are updated to include it.

## Package verification boundary

- Python syntax compilation was performed while preparing the package.
- No unit tests, API tests, PostgreSQL tests, migrations, or application flows were executed.
- No Git commit or push was performed.
- The overlay does not contain changes to `001_initial_mvp.sql`, `002_harden_mvp.sql`, `004_pre_invoices.sql`, `005_cancel_constraints.sql`, `006_request_cancel_guard.sql`, or `runner.py`.
- Because database tests were intentionally not run, treat the migration and transactional integration as unverified until the project's PostgreSQL test suite is run in the user's test environment.
