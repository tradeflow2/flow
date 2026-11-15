TradeFlow / Flow1 - Stage 5 overlay

این بسته یک Overlay است، نه نسخه کامل مخزن.
محتویات ZIP را در ریشه مخزن استخراج و فایل‌های هم‌مسیر را جایگزین کنید.

فایل‌های جدید:
- db/sql/007_stage5.sql
- core/tradeflow_core/discount.py
- core/tradeflow_core/approval.py
- core/tradeflow_core/snapshot.py
- core/tests/test_discount.py
- core/tests/test_approval.py
- core/tests/test_pg_stage5.py
- api/app/discount_service.py
- api/app/approval_service.py
- api/app/snapshot_service.py
- api/app/commercial_mutations.py
- api/app/routes/discounts.py
- api/app/routes/approvals.py
- api/app/routes/discount_policies.py
- api/app/routes/snapshots.py
- api/tests/test_discounts_http.py
- api/tests/test_approvals_http.py
- docs/STAGE5_IMPLEMENTATION.md

فایل‌های جایگزین‌شونده:
- docs/ADR-064.md
- core/tradeflow_core/errors.py
- core/tests/pg.py
- db/tests/test_runner_order.py
- api/app/db.py
- api/app/main.py
- api/app/requests_service.py
- api/app/pre_invoices_service.py
- api/tests/pg.py
- api/tests/support.py

مرزهای محافظت‌شده در این بسته وجود ندارند و نباید تغییر داده شوند:
001_initial_mvp.sql
002_harden_mvp.sql
004_pre_invoices.sql
005_cancel_constraints.sql
006_request_cancel_guard.sql
runner.py

تست‌ها اجرا نشده‌اند. فقط بررسی Syntax پایتون انجام شده است.
هیچ Migration روی دیتابیس اجرا نشده و Commit/Push صورت نگرفته است.
