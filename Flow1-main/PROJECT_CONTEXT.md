# Flow1 — Project Context Pack
Last updated: 2026-11-16
Current commit: 6e0f9d2 (WIP Stage 6 S6-A)
Repository: https://github.com/tradeflow2/flow
Environment: Windows 10 + VS Code + PostgreSQL 16

## 1. Vision & Scope
- VISION v4: 14 MVP features
- Stage 1-5: DONE
- Stage 6: IN PROGRESS (S6-A: Payments)
- Stage 7-8: NOT STARTED

## 2. Architecture
- Layers: db (SQL) -> core (domain) -> api (FastAPI) -> tests
- Key modules:
  - core/tradeflow_core/: authz, discount, approval, snapshot, payment, pre_invoice, ...
  - api/app/: services, routes, mutations
  - db/sql/: 001-008 migrations (007=Stage5, 008=Stage6)
- Protected files (DO NOT MODIFY): 001, 002, 004, 005, 006, runner.py

## 3. Stage 5 — DONE (commit 7fd5d1b)
- 319 tests passing (186 core + 112 api + 21 runner)
- 8 tables in Migration 007
- Key decisions: 16 items

## 4. Stage 6 — IN PROGRESS
### Sub-stages:
- S6-A: Payments (IN PROGRESS)
- S6-B: Financial Confirmation (NOT STARTED)
- S6-C: Credit (NOT STARTED)

### Files created (all committed at 6e0f9d2):
- db/sql/008_stage6_payments.sql
- core/tradeflow_core/payment.py
- api/app/payment_service.py
- api/app/routes/payments.py
- core/tests/test_payment.py
- core/tests/test_pg_payment.py
- api/tests/test_payments_http.py
- docs/STAGE_06_CONTRACT.md
- docs/ADR-065.md

### Modified:
- core/tradeflow_core/authz.py (PAYMENT_CREATE, PAYMENT_READ)
- core/tests/test_policy.py (PAYMENT_* in EXPECT and mapping)
- api/app/main.py (payment_routes registered)
- api/app/serialize.py
- db/tests/test_runner_order.py
- PROJECT_STATE.md

### Test status (as of 2026-11-16):
| File | Status |
|------|--------|
| core/tests/test_payment.py | 19/19 PASS |
| core/tests/test_pg_payment.py | 5/5 PASS |
| core/tests/test_policy.py | 22/22 PASS |
| api/tests/test_payments_http.py | 8 FAILED, 1 PASSED |

### Known issues (API tests):
| # | Issue | Cause |
|---|-------|-------|
| 1 | 008_stage6_payments.sql not in make_db | api/tests/pg.py needs file added |
| 2 | pre_invoices empty in tf_api_test | setUpClass needs to create pre_invoice |

### Solutions decided by ChatGPT:
1. Add "008_stage6_payments.sql" to files list in api/tests/pg.py make_db
2. In setUpClass of test_payments_http.py: create purchase_request (DRAFT) -> item -> SUBMITTED -> pre_invoice (DRAFT) -> items -> ISSUED (following test_pg_payment.py pattern)
3. support.py unchanged
4. No architecture, Migration, or protected file changes

## 5. Key Design Decisions (Stage 6, ADR-065)
- 3 payment methods: CASH, BANK_TRANSFER, CHEQUE
- Multiple payment methods per Pre-Invoice allowed
- Partial payments allowed
- Cheque lifecycle: RECEIVED -> DEPOSITED -> CLEARED | BOUNCED
- Cheque not counted as collected until CLEARED
- Payment does NOT authorize physical release
- Actual refunds outside MVP
- credit_limit must be removed, credit_agreements authoritative
- Migration 008: backfill non-zero limits -> PENDING_APPROVAL, then remove column

## 6. DB triggers (important for tests)
- trg_purchase_request_items_guard: items can only be added while purchase_request is DRAFT
- trg_pre_invoices_guard:
  - Pre-invoice can only be INSERTed as DRAFT
  - UPDATE to ISSUED requires: sum(pre_invoice_items) == grand_total
- No trigger for request_number in tf_core_pi or tf_api_test -> must supply manually

## 7. How to run tests
cd C:\Users\10\Flow1\Flow1-main
$env:PGHOST="localhost"; $env:PGPORT="5432"; $env:PGUSER="postgres"; $env:PGPASSWORD="postgres"
$env:PYTHONPATH="$PWD\api;$PWD\core"; $env:TF_SQL_DIR="$PWD\db\sql"

pytest core/tests -q
pytest api/tests -q
pytest db/tests/test_runner_order.py -q

## 8. How to restore context (next chat)
1. Attach this file (PROJECT_CONTEXT.md)
2. Attach SESSION_LOG.md
3. Attach last error traceback
4. Attach specific file being worked on
