"""PostgreSQL tests for payments (Stage 6, S6-A). Every scenario runs inside BEGIN ... ROLLBACK."""
from __future__ import annotations

import os
import unittest
from datetime import date, timedelta

from . import pg
from .test_pg_integration import _ensure_db


SQL_DIR = os.environ.get("TF_SQL_DIR")

PI_ID = "00000000-0000-0000-0000-0000000000b1"
USER_ID = "00000000-0000-0000-0000-0000000000c1"


@unittest.skipUnless(pg.available() and SQL_DIR, "SKIPPED: needs PostgreSQL via psql and TF_SQL_DIR")
class PaymentsPg(unittest.TestCase):
    DB = "tf_core_pi"

    @classmethod
    def setUpClass(cls):
        _ensure_db(cls.DB)
        # 1. Get org + admin + customer from seed (these DO exist)
        rc, out, e = pg.psql(cls.DB, """
            SELECT o.id, u.id, c.id
              FROM organizations o
              JOIN users u ON u.organization_id = o.id AND u.username = 'admin'
              JOIN customers c ON c.organization_id = o.id
             WHERE o.code = 'DEMO'
             LIMIT 1
        """)
        assert rc == 0, e
        parts = out.strip().split("|")
        assert len(parts) == 3, f"Expected 3 (org, admin, customer), got {len(parts)}: {out!r} / {e!r}"
        cls.org, cls.admin, cls.cust = parts

        # 2. Create a DRAFT purchase_request first (items can only be added while DRAFT)
        rc, out, e = pg.psql(cls.DB, f"""
            INSERT INTO purchase_requests (
                organization_id, customer_id, requested_delivery_date,
                status, created_by, notes, request_number
            ) VALUES (
                '{cls.org}', '{cls.cust}', CURRENT_DATE,
                'DRAFT', '{cls.admin}', 'test fixture for payments',
                'TEST-PAY-' || to_char(clock_timestamp(), 'YYYYMMDDHH24MISSUS')
            )
            RETURNING id
        """)
        assert rc == 0, e
        cls.req = out.strip()

        # 3. Create a purchase_request_item while DRAFT is still allowed
        rc, out, e = pg.psql(cls.DB, f"""
            INSERT INTO purchase_request_items (
                organization_id, purchase_request_id, product_id,
                unit_id, quantity, unit_price, discount_amount,
                tax_amount, line_total, bundle_count
            ) SELECT
                r.organization_id, r.id, p.id, p.default_unit_id,
                1, 1000000, 0, 0, 1000000, 0
            FROM purchase_requests r
            JOIN products p ON p.organization_id = r.organization_id
            WHERE r.id = '{cls.req}'
            LIMIT 1
            RETURNING id
        """)
        assert rc == 0, e

        # 4. Now transition DRAFT -> SUBMITTED (items are frozen after this)
        rc, out, e = pg.psql(cls.DB, f"""
            UPDATE purchase_requests
            SET status='SUBMITTED'
            WHERE id = '{cls.req}'
        """)
        assert rc == 0, e

    # ------------------------------------------------------------------ helpers
    def provoke(self, setup: str, failing: str):
        statement = failing.strip().rstrip(";")
        body = f"""BEGIN;
        {setup or 'SELECT 1;'}
        DO $$ DECLARE s TEXT; c TEXT; m TEXT; BEGIN
          BEGIN
            {statement};
            RAISE NOTICE 'RES|NONE||';
          EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS s = RETURNED_SQLSTATE,
                                    c = CONSTRAINT_NAME,
                                    m = MESSAGE_TEXT;
            RAISE NOTICE 'RES|%|%|%', s, coalesce(c,''), replace(m, E'\\n', ' ');
          END;
        END $$;
        ROLLBACK;"""
        rc, out, err = pg.psql(self.DB, body)
        self.assertEqual(rc, 0, err)
        line = next(l for l in err.splitlines() if "RES|" in l)
        s, c, m = line.split("RES|", 1)[1].split("|", 2)
        return s, c or None, m

    def fails(self, setup, failing, sqlstate, constraint=None):
        s, c, m = self.provoke(setup, failing)
        self.assertEqual(s, sqlstate, f"{failing} -> {s} {c} {m}")
        if constraint:
            self.assertEqual(c, constraint, f"{failing} -> {s} {c} {m}")
        return s, c, m

    def state_conflict(self, setup, failing):
        s, c, m = self.fails(setup, failing, "55000")
        return s, c, m

    def create_pre_invoice(self):
        """Create an ISSUED Pre-Invoice (DRAFT -> items -> ISSUED), following Stage 4 pattern."""
        insert_draft = (
            f"INSERT INTO pre_invoices ("
            f"  id, organization_id, source_request_id, chain_id, "
            f"  request_number, request_date, seller_name, "
            f"  customer_id, customer_type, customer_name, "
            f"  subtotal, discount_total, tax_total, grand_total, created_by"
            f") SELECT "
            f"  '{PI_ID}', r.organization_id, r.id, '{PI_ID}', "
            f"  r.request_number, r.request_date, o.name, "
            f"  c.id, c.customer_type, c.name, "
            f"  1000000, 0, 0, 1000000, '{self.admin}' "
            f"  FROM purchase_requests r "
            f"  JOIN organizations o ON o.id = r.organization_id "
            f"  JOIN customers c ON c.id = r.customer_id "
            f"  WHERE r.id = '{self.req}';"
        )
        insert_items = (
            f"INSERT INTO pre_invoice_items ("
            f"  organization_id, pre_invoice_id, line_no, source_item_id, "
            f"  product_id, product_code, product_name, unit_id, "
            f"  unit_code, unit_name, quantity, bundle_count, unit_price, "
            f"  discount_amount, tax_amount, line_total"
            f") SELECT "
            f"  i.organization_id, '{PI_ID}', 1, i.id, "
            f"  i.product_id, 'P', 'P', i.unit_id, "
            f"  'KG', 'Kg', i.quantity, i.bundle_count, i.unit_price, "
            f"  i.discount_amount, i.tax_amount, i.line_total "
            f"  FROM purchase_request_items i "
            f"  WHERE i.purchase_request_id = '{self.req}';"
        )
        issue = (
            f"UPDATE pre_invoices "
            f"SET status='ISSUED', "
            f"    pre_invoice_number='PI-1405-000001', "
            f"    version=1, issued_by='{self.admin}', "
            f"    issued_at=NOW() "
            f"WHERE id='{PI_ID}';"
        )
        return insert_draft + insert_items + issue

    def insert_payment(self, method="CASH", amount=1000000, **kw):
        pid = "00000000-0000-0000-0000-0000000000d1"
        if "cheque_number" in kw:
            cheque_number = f"'{kw['cheque_number']}'"
        else:
            cheque_number = "'123'" if method == "CHEQUE" else "NULL"
        if "cheque_due_date" in kw:
            cheque_due_date = f"'{kw['cheque_due_date']}'"
        else:
            cheque_due_date = f"'{(date.today() + timedelta(days=30)).isoformat()}'" if method == "CHEQUE" else "NULL"
        if "cheque_status" in kw:
            cheque_status = f"'{kw['cheque_status']}'"
        else:
            cheque_status = "'RECEIVED'" if method == "CHEQUE" else "NULL"
        return (
            f"INSERT INTO payments ("
            f"  id, organization_id, pre_invoice_id, payment_method, "
            f"  amount, payment_date, cheque_number, cheque_due_date, cheque_status, "
            f"  status, created_by"
            f") VALUES ("
            f"  '{pid}', '{self.org}', '{PI_ID}', '{method}', "
            f"  {amount}, '{date.today().isoformat()}', "
            f"  {cheque_number}, {cheque_due_date}, {cheque_status}, "
            f"  'PENDING_CONFIRMATION', '{self.admin}'"
            f");"
        )

    # ------------------------------------------------------------------ tests
    def test_delete_payment_rejected(self):
        self.state_conflict(
            self.create_pre_invoice() + self.insert_payment(),
            f"DELETE FROM payments WHERE pre_invoice_id = '{PI_ID}'",
        )

    def test_update_confirmed_payment_rejected(self):
        setup = (
            self.create_pre_invoice()
            + self.insert_payment()
            + f"UPDATE payments SET status='CONFIRMED', confirmed_by='{self.admin}', confirmed_at=NOW() "
            f"WHERE pre_invoice_id = '{PI_ID}';"
        )
        self.state_conflict(
            setup,
            f"UPDATE payments SET amount = 2000000 WHERE pre_invoice_id = '{PI_ID}'",
        )

    def test_check_cash_with_cheque_fields_rejected(self):
        self.fails(
            self.create_pre_invoice(),
            self.insert_payment(method="CASH", cheque_number="123"),
            "23514",
            "chk_payments_method_fields",
        )

    def test_check_amount_positive_rejected(self):
        self.fails(
            self.create_pre_invoice(),
            self.insert_payment(amount=0),
            "23514",
            "chk_payments_amount",
        )

    def test_fk_pre_invoice_rejected(self):
        self.fails(
            "",
            f"INSERT INTO payments ("
            f"  id, organization_id, pre_invoice_id, payment_method, "
            f"  amount, payment_date, status, created_by"
            f") VALUES ("
            f"  gen_random_uuid(), '{self.org}', gen_random_uuid(), 'CASH', "
            f"  1000000, '{date.today().isoformat()}', "
            f"  'PENDING_CONFIRMATION', '{self.admin}'"
            f");",
            "23503",
        )


if __name__ == "__main__":
    unittest.main()