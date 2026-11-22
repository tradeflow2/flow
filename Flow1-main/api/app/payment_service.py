"""Payment registration service for Stage 6 (S6-A).

One HTTP request = one transaction. All writes go through the database
connection provided by `Database.run`.

Related: ADR-065 (S6-A), ADR-058 (money), ADR-012 (payment != release),
ADR-014 (audit), ADR-024 (tenant isolation).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from tradeflow_core import inputs
from tradeflow_core import payment as pay
from tradeflow_core.authz import Cap, authorize
from tradeflow_core.errors import AppError, ErrorCode

from . import serialize
from .queries import not_found, page_args, write_audit


_PAYMENT_SQL = """
SELECT p.*, pi.pre_invoice_number, pi.grand_total AS pre_invoice_total
  FROM payments p
  JOIN pre_invoices pi
    ON pi.id = p.pre_invoice_id
   AND pi.organization_id = p.organization_id
 WHERE p.organization_id = %s
"""


class PaymentService:
    def __init__(self, db, clock=lambda: datetime.now(timezone.utc)):
        self.db = db
        self.clock = clock

    # ------------------------------------------------------------------ helpers
    def _load_pre_invoice(self, conn, p, pre_invoice_id: str):
        row = conn.execute(
            "SELECT id, organization_id, status, grand_total, pre_invoice_number "
            "  FROM pre_invoices "
            " WHERE id = %s AND organization_id = %s",
            (pre_invoice_id, p.org_id),
        ).fetchone()
        if row is None:
            raise not_found("pre_invoice")
        return row

    def _allocated(self, conn, p, pre_invoice_id: str) -> tuple[Decimal, Decimal, Decimal]:
        """Return (confirmed, pending, credit) totals for the Pre-Invoice."""
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(amount) FILTER (WHERE status = 'CONFIRMED'), 0) AS confirmed,
              COALESCE(SUM(amount) FILTER (WHERE status = 'PENDING_CONFIRMATION'), 0) AS pending
              FROM payments
             WHERE organization_id = %s AND pre_invoice_id = %s
            """,
            (p.org_id, pre_invoice_id),
        ).fetchone()
        confirmed = Decimal(row["confirmed"] or 0)
        pending = Decimal(row["pending"] or 0)
        # Credit allocation is out of scope for S6-A; 0 for now.
        credit = Decimal(0)
        return confirmed, pending, credit

    def _payment_row(self, conn, p, payment_id: str, lock: bool = False):
        sql = _PAYMENT_SQL + " AND p.id = %s" + (" FOR UPDATE OF p" if lock else "")
        row = conn.execute(sql, (p.org_id, payment_id)).fetchone()
        if row is None:
            raise not_found("payment")
        return row

    def _detail(self, conn, p, payment_id: str, row=None) -> dict:
        row = row or self._payment_row(conn, p, payment_id)
        return serialize.payment(row)

    # ------------------------------------------------------------------ create
    def create(self, p, pre_invoice_id: str, body: dict, ip: str) -> dict:
        authorize(p.role_code, Cap.PAYMENT_CREATE)
        pre_invoice_id = inputs.parse_uuid(pre_invoice_id, "id")
        v = pay.parse_create(body)

        if v["pre_invoice_id"] != pre_invoice_id:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details=[{"field": "pre_invoice_id", "reason": "MISMATCH"}],
            )

        def work(conn):
            pre = self._load_pre_invoice(conn, p, pre_invoice_id)
            if pre["status"] == "CANCELLED":
                raise AppError(
                    ErrorCode.STATE_CONFLICT,
                    details={"reason": "PRE_INVOICE_CANCELLED"},
                )

            confirmed, pending, credit = self._allocated(conn, p, pre_invoice_id)
            pay.validate_allocation(
                pre_invoice_total=Decimal(pre["grand_total"]),
                existing_confirmed=confirmed,
                existing_pending=pending,
                existing_credit=credit,
                new_amount=v["amount"],
            )

            new_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO payments (
                    id, organization_id, pre_invoice_id, payment_method,
                    amount, payment_date, reference_number, payer_name,
                    bank_name, cheque_number, cheque_due_date, cheque_status,
                    status, notes, created_by
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    'PENDING_CONFIRMATION', %s, %s
                )
                """,
                (
                    new_id, p.org_id, pre_invoice_id, v["payment_method"],
                    v["amount"], v["payment_date"], v["reference_number"],
                    v["payer_name"], v["bank_name"], v["cheque_number"],
                    v["cheque_due_date"], v["cheque_status"],
                    v["notes"], p.user_id,
                ),
            )
            out = self._detail(conn, p, new_id)
            write_audit(
                conn, p, "CREATE", "PAYMENT", new_id,
                new={**out, "pre_invoice_number": pre["pre_invoice_number"]},
                ip=ip,
            )
            return out

        return self.db.run(work)

    # ------------------------------------------------------------------ read
    def list(self, p, pre_invoice_id: str, query) -> tuple[list[dict], int, int, int]:
        authorize(p.role_code, Cap.PAYMENT_READ)
        pre_invoice_id = inputs.parse_uuid(pre_invoice_id, "id")
        page, size, offset = page_args(query)

        def work(conn):
            self._load_pre_invoice(conn, p, pre_invoice_id)
            total = conn.execute(
                "SELECT count(*) AS n FROM payments "
                " WHERE organization_id = %s AND pre_invoice_id = %s",
                (p.org_id, pre_invoice_id),
            ).fetchone()["n"]
            rows = conn.execute(
                _PAYMENT_SQL
                + " AND p.pre_invoice_id = %s"
                + " ORDER BY p.created_at DESC, p.id DESC"
                + " LIMIT %s OFFSET %s",
                (p.org_id, pre_invoice_id, size, offset),
            ).fetchall()
            return [serialize.payment(r) for r in rows], total

        items, total = self.db.run(work)
        return items, page, size, total

    def get(self, p, payment_id: str) -> dict:
        authorize(p.role_code, Cap.PAYMENT_READ)
        payment_id = inputs.parse_uuid(payment_id, "id")
        return self.db.run(lambda conn: self._detail(conn, p, payment_id))

    # ------------------------------------------------------------------ patch
    def patch(self, p, payment_id: str, body: dict, ip: str) -> dict:
        authorize(p.role_code, Cap.PAYMENT_CREATE)
        payment_id = inputs.parse_uuid(payment_id, "id")

        def work(conn):
            current = self._payment_row(conn, p, payment_id, lock=True)
            current_d = serialize.payment(current)
            changes = pay.parse_patch(body, current_d)
            if not changes:
                return current_d

            if "amount" in changes:
                pre = self._load_pre_invoice(conn, p, str(current["pre_invoice_id"]))
                confirmed, pending, credit = self._allocated(conn, p, str(current["pre_invoice_id"]))
                # Subtract the old amount from pending
                pending = pending - Decimal(current["amount"])
                pay.validate_allocation(
                    pre_invoice_total=Decimal(pre["grand_total"]),
                    existing_confirmed=confirmed,
                    existing_pending=pending,
                    existing_credit=credit,
                    new_amount=changes["amount"],
                )

            if not changes:
                return current_d

            set_sql = ", ".join(f"{k} = %s" for k in changes)
            values = list(changes.values()) + [payment_id, p.org_id]
            conn.execute(
                f"UPDATE payments SET {set_sql} WHERE id = %s AND organization_id = %s",
                values,
            )
            out = self._detail(conn, p, payment_id)
            write_audit(
                conn, p, "UPDATE", "PAYMENT", payment_id,
                old={k: current_d.get(k) for k in changes},
                new={k: out.get(k) for k in changes},
                ip=ip,
            )
            return out

        return self.db.run(work)