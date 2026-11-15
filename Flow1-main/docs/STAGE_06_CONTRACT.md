# STAGE_06_CONTRACT.md — S6-A: Payments

**Project:** Flow1
**Stage:** 6 (S6-A)
**Status:** Proposed Contract
**Date:** 2026-11-15
**Migration:** 008
**Related:** ADR-065, ADR-058, ADR-012, ADR-014, ADR-024

---

## 1. Scope

S6-A covers **payment registration** for Pre-Invoices.

Out of scope (S6-B and S6-C):
- Financial confirmation workflow.
- Cheque lifecycle transitions (beyond initial registration).
- Credit agreements and credit allocation.
- Customer refunds.
- Accounting system integration.

S6-A produces:
- `payments` table (Migration 008).
- Core: `core/tradeflow_core/payment.py`.
- API: `api/app/payment_service.py` and routes.
- Tests: Core, API, PostgreSQL.

---

## 2. Confirmed Decisions (from ADR-065)

| # | Decision |
|---|----------|
| 1 | Payment methods: `CASH`, `BANK_TRANSFER`, `CHEQUE` |
| 2 | Multiple payments per Pre-Invoice |
| 3 | Partial payments supported |
| 4 | Total allocated payment + credit ≤ Pre-Invoice total |
| 5 | Registered payments cannot be physically deleted |
| 6 | Confirmed payments cannot be silently edited |
| 7 | Corrections require reversal or new record |

---

## 3. Data Model: `payments`

### 3.1 Columns

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | UUID | NOT NULL | `gen_random_uuid()` | PK |
| `organization_id` | UUID | NOT NULL | — | Tenant |
| `pre_invoice_id` | UUID | NOT NULL | — | FK → `pre_invoices(id)` |
| `payment_method` | VARCHAR(20) | NOT NULL | — | `CASH`, `BANK_TRANSFER`, `CHEQUE` |
| `amount` | NUMERIC(20,0) | NOT NULL | — | Rial, positive |
| `payment_date` | DATE | NOT NULL | — | — |
| `reference_number` | VARCHAR(100) | NULL | — | For BANK_TRANSFER |
| `payer_name` | VARCHAR(255) | NULL | — | — |
| `bank_name` | VARCHAR(100) | NULL | — | For BANK_TRANSFER, CHEQUE |
| `cheque_number` | VARCHAR(50) | NULL | — | For CHEQUE |
| `cheque_due_date` | DATE | NULL | — | For CHEQUE |
| `cheque_status` | VARCHAR(20) | NULL | — | `RECEIVED`, `DEPOSITED`, `CLEARED`, `BOUNCED` |
| `status` | VARCHAR(30) | NOT NULL | `PENDING_CONFIRMATION` | `PENDING_CONFIRMATION`, `CONFIRMED`, `REJECTED`, `REVERSED` |
| `notes` | TEXT | NULL | — | — |
| `created_by` | UUID | NOT NULL | — | FK → `users(id)` |
| `created_at` | TIMESTAMPTZ | NOT NULL | `NOW()` | — |
| `confirmed_by` | UUID | NULL | — | FK → `users(id)` |
| `confirmed_at` | TIMESTAMPTZ | NULL | — | — |
| `reversed_by` | UUID | NULL | — | FK → `users(id)` |
| `reversed_at` | TIMESTAMPTZ | NULL | — | — |
| `reversal_reason` | TEXT | NULL | — | Required if REVERSED |

### 3.2 Constraints

- **PK:** `id`
- **UNIQUE:** `(id, organization_id)`
- **FK:** `(pre_invoice_id, organization_id)` → `pre_invoices(id, organization_id)`
- **FK:** `(created_by, organization_id)` → `users(id, organization_id)`
- **FK:** `(confirmed_by, organization_id)` → `users(id, organization_id)`
- **FK:** `(reversed_by, organization_id)` → `users(id, organization_id)`
- **CHECK:** `amount > 0`
- **CHECK:** `payment_method IN ('CASH', 'BANK_TRANSFER', 'CHEQUE')`
- **CHECK:** `status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'REJECTED', 'REVERSED')`
- **CHECK:** `cheque_status IS NULL OR cheque_status IN ('RECEIVED', 'DEPOSITED', 'CLEARED', 'BOUNCED')`
- **CHECK:** Method-specific validation:
  - `CASH`: `cheque_number IS NULL AND cheque_due_date IS NULL AND cheque_status IS NULL`
  - `BANK_TRANSFER`: `cheque_number IS NULL AND cheque_due_date IS NULL AND cheque_status IS NULL`
  - `CHEQUE`: `cheque_number IS NOT NULL AND cheque_due_date IS NOT NULL AND cheque_status IS NOT NULL`
- **CHECK:** Status-specific:
  - `CONFIRMED`: `confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL`
  - `REJECTED`: `confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL` (rejection uses same fields)
  - `REVERSED`: `reversed_by IS NOT NULL AND reversed_at IS NOT NULL AND reversal_reason IS NOT NULL`
- **CHECK:** `payment_date <= CURRENT_DATE + INTERVAL '1 day'` (can't be future)
- **CHECK:** `cheque_due_date IS NULL OR cheque_due_date >= payment_date`

### 3.3 Indexes

- `idx_payments_pre_invoice` ON `(organization_id, pre_invoice_id)`
- `idx_payments_status` ON `(organization_id, status)`
- `idx_payments_created_by` ON `(organization_id, created_by)`

---

## 4. Payment Rules

### 4.1 Registration Rules

- Payment belongs to exactly one organization.
- Payment belongs to exactly one Pre-Invoice.
- Payment method must be one of the three allowed.
- Amount must be positive.
- Payment date cannot be in the future.

### 4.2 Allocation Rules

- Total of all `CONFIRMED` payments + total of all `PENDING_CONFIRMATION` payments + total of all `credit_allocations` (from S6-C) must not exceed Pre-Invoice `grand_total`.
- If allocation would exceed `grand_total`, reject with `55000` (state conflict).

### 4.3 Method-Specific Rules

| Method | Required Fields | Forbidden Fields |
|--------|-----------------|------------------|
| `CASH` | `amount`, `payment_date` | `cheque_number`, `cheque_due_date`, `cheque_status` |
| `BANK_TRANSFER` | `amount`, `payment_date`, `reference_number`, `bank_name` | `cheque_number`, `cheque_due_date`, `cheque_status` |
| `CHEQUE` | `amount`, `payment_date`, `cheque_number`, `bank_name`, `cheque_due_date` | — |

- For `CHEQUE`, initial `cheque_status` must be `RECEIVED`.

### 4.4 Deletion and Edit Rules

- `DELETE` on `payments` is forbidden (trigger with `55000`).
- `UPDATE` on `CONFIRMED` or `REVERSED` payments is forbidden (trigger with `55000`).
- `UPDATE` on `PENDING_CONFIRMATION` is allowed (before confirmation).

---

## 5. Core: `core/tradeflow_core/payment.py`

### 5.1 Functions

```python
def parse_create(body: dict) -> dict:
    """Validate and normalize a payment creation payload."""

def validate_method_fields(payment_method: str, fields: dict) -> None:
    """Validate method-specific fields."""

def validate_amount(amount: Decimal) -> None:
    """Validate amount > 0."""

def validate_payment_date(payment_date: date) -> None:
    """Validate payment_date not in future."""