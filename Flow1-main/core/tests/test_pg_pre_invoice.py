"""Migrations 004-006 against the REAL PostgreSQL: constraints, triggers,
composite organization foreign keys, partial unique indexes, immutability
by direct SQL, PDF integrity, counter series and request-cancellation guard.
Every scenario runs inside BEGIN ... ROLLBACK.
WRITTEN, NOT YET RUN by its author - the owner runs it (core/run_tests.sh).
"""

import os
import unittest

from tradeflow_core.errors import ErrorCode, map_pg_error
from . import pg
from .test_pg_integration import _ensure_db

SQL_DIR = os.environ.get("TF_SQL_DIR")

R1, R2 = (
    "00000000-0000-0000-0000-0000000000a1",
    "00000000-0000-0000-0000-0000000000a2",
)
P1, P2, P3 = (
    "00000000-0000-0000-0000-0000000000b1",
    "00000000-0000-0000-0000-0000000000b2",
    "00000000-0000-0000-0000-0000000000b3",
)
PDF = "convert_to('%PDF-1.7 test document', 'UTF8')"


@unittest.skipUnless(
    pg.available() and SQL_DIR,
    "SKIPPED: needs PostgreSQL via psql and TF_SQL_DIR",
)
class Migration004(unittest.TestCase):
    DB = "tf_core_pi"

    @classmethod
    def setUpClass(cls):
        _ensure_db(cls.DB)
        rc, out, e = pg.psql(
            cls.DB,
            """
            SELECT o.id, p.id, u.id, c.id, usr.id
            FROM organizations o
            JOIN products p
              ON p.organization_id = o.id
             AND p.code = 'GAS-PIPE-2IN'
            JOIN units u
              ON u.organization_id = o.id
             AND u.code = 'KG'
            JOIN customers c
              ON c.organization_id = o.id
            JOIN users usr
              ON usr.organization_id = o.id
             AND usr.username = 'admin'
            WHERE o.code = 'DEMO'
            """,
        )
        assert rc == 0, e
        (
            cls.org,
            cls.prod,
            cls.unit,
            cls.cust,
            cls.admin,
        ) = out.strip().splitlines()[0].split("|")

    # ------------------------------------------------------------------
    # Scenario builders (SQL text, run inside one transaction)
    # ------------------------------------------------------------------

    def request(self, rid, number, status="SUBMITTED"):
        sql = (
            f"INSERT INTO purchase_requests "
            f"(id, organization_id, request_number, customer_id, "
            f"created_by, updated_by) "
            f"VALUES ('{rid}','{self.org}','{number}','{self.cust}',"
            f"'{self.admin}','{self.admin}');"
            f"INSERT INTO purchase_request_items "
            f"(organization_id, purchase_request_id, product_id, "
            f"unit_id, quantity, unit_price, discount_amount, "
            f"tax_amount, line_total, bundle_count) "
            f"VALUES ('{self.org}','{rid}','{self.prod}','{self.unit}',"
            f"1, 10, 0, 0, 10, 0);"
        )

        if status != "DRAFT":
            sql += (
                f"UPDATE purchase_requests "
                f"SET status='SUBMITTED' WHERE id='{rid}';"
            )

        if status == "CANCELLED":
            sql += (
                f"UPDATE purchase_requests "
                f"SET status='CANCELLED' WHERE id='{rid}';"
            )

        return sql

    def draft(
        self,
        pid,
        rid,
        chain=None,
        supersedes=None,
        with_items=True,
        status=None,
    ):
        sup = f"'{supersedes}'" if supersedes else "NULL"
        st_col, st_val = (
            ("status, ", f"'{status}', ")
            if status
            else ("", "")
        )

        sql = (
            f"INSERT INTO pre_invoices "
            f"({st_col}id, organization_id, source_request_id, "
            f"chain_id, supersedes_id, request_number, request_date, "
            f"seller_name, customer_id, customer_type, customer_name, "
            f"subtotal, discount_total, tax_total, grand_total, created_by) "
            f"SELECT {st_val}'{pid}', r.organization_id, r.id, "
            f"'{chain or pid}', {sup}, r.request_number, r.request_date, "
            f"o.name, c.id, c.customer_type, c.name, r.subtotal, "
            f"r.discount_total, r.tax_total, r.grand_total, r.created_by "
            f"FROM purchase_requests r "
            f"JOIN organizations o ON o.id = r.organization_id "
            f"JOIN customers c ON c.id = r.customer_id "
            f"WHERE r.id = '{rid}';"
        )

        if with_items:
            sql += (
                f"INSERT INTO pre_invoice_items "
                f"(organization_id, pre_invoice_id, line_no, source_item_id, "
                f"product_id, product_code, product_name, unit_id, "
                f"unit_code, unit_name, quantity, bundle_count, unit_price, "
                f"discount_amount, tax_amount, line_total) "
                f"SELECT i.organization_id, '{pid}', 1, i.id, i.product_id, "
                f"'P', 'P', i.unit_id, 'KG', 'Kg', i.quantity, "
                f"i.bundle_count, i.unit_price, i.discount_amount, "
                f"i.tax_amount, i.line_total "
                f"FROM purchase_request_items i "
                f"WHERE i.purchase_request_id = '{rid}';"
            )

        return sql

    def issue(self, pid, number="PI-1405-000001", version=1):
        return (
            f"UPDATE pre_invoices "
            f"SET status='ISSUED', pre_invoice_number='{number}', "
            f"version={version}, issued_by='{self.admin}', "
            f"issued_at=NOW() WHERE id='{pid}';"
        )

    def supersede(self, pid):
        return (
            f"UPDATE pre_invoices "
            f"SET status='SUPERSEDED', superseded_at=NOW() "
            f"WHERE id='{pid}';"
        )

    def cancel(self, pid, reason="NULL"):
        return (
            f"UPDATE pre_invoices "
            f"SET status='CANCELLED', cancelled_by='{self.admin}', "
            f"cancelled_at=NOW(), cancel_reason={reason} "
            f"WHERE id='{pid}';"
        )

    def document(self, pid):
        return (
            f"INSERT INTO pre_invoice_documents "
            f"(organization_id, pre_invoice_id) "
            f"VALUES ('{self.org}','{pid}');"
        )

    def issued(self):
        """R1 submitted, P1 issued with a pending document."""
        return (
            self.request(R1, "T-000001")
            + self.draft(P1, R1)
            + self.issue(P1)
            + self.document(P1)
        )

    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------

    def provoke(self, setup: str, failing: str):
        body = f"""BEGIN;
        {setup}
        DO $$ DECLARE s TEXT; c TEXT; m TEXT; BEGIN
          BEGIN {failing}; RAISE NOTICE 'RES|NONE||';
          EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS
              s = RETURNED_SQLSTATE,
              c = CONSTRAINT_NAME,
              m = MESSAGE_TEXT;
            RAISE NOTICE 'RES|%|%|%',
              s, coalesce(c,''), replace(m, E'\\\\n', ' ');
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

        self.assertEqual(
            s,
            sqlstate,
            f"{failing} -> {s} {c} {m}",
        )

        if constraint:
            self.assertEqual(
                c,
                constraint,
                f"{failing} -> {s} {c} {m}",
            )

        return map_pg_error(s, c, m)

    def state_conflict(self, setup, failing):
        e = self.fails(setup, failing, "55000")
        self.assertEqual(e.code, ErrorCode.STATE_CONFLICT)

    def internal(self, setup, failing, constraint):
        e = self.fails(setup, failing, "23514", constraint)
        self.assertEqual(
            e.code,
            ErrorCode.INTERNAL_ERROR,
            constraint,
        )

    def ok(self, script):
        rc, out, err = pg.psql(
            self.DB,
            "BEGIN;\n" + script + "\nROLLBACK;",
        )
        self.assertEqual(rc, 0, err)
        return out

    # ------------------------------------------------------------------
    # Happy paths
    # ------------------------------------------------------------------

    def test_issue_and_pdf_ready_happy_path(self):
        self.ok(
            self.issued()
            + f"""
            UPDATE pre_invoice_documents
            SET attempt_count = 1, last_attempt_at = NOW()
            WHERE pre_invoice_id = '{P1}';

            UPDATE pre_invoice_documents
            SET status = 'ready',
                pdf_data = {PDF},
                pdf_size = octet_length({PDF}),
                pdf_sha256 = encode(sha256({PDF}), 'hex'),
                template_version = 'pi-1',
                generated_at = NOW()
            WHERE pre_invoice_id = '{P1}';

            DO $$ BEGIN
              IF (
                SELECT status
                FROM pre_invoice_documents
                WHERE pre_invoice_id = '{P1}'
              ) <> 'ready' THEN
                RAISE EXCEPTION 'not ready';
              END IF;

              IF (
                SELECT status
                FROM pre_invoices
                WHERE id = '{P1}'
              ) <> 'ISSUED' THEN
                RAISE EXCEPTION 'commercial status changed';
              END IF;
            END $$;
            """
        )

    def test_replacement_chain_happy_path(self):
        self.ok(
            self.issued()
            + self.request(R2, "T-000002")
            + self.draft(P2, R2, chain=P1, supersedes=P1)
            + self.supersede(P1)
            + self.issue(P2, "PI-1405-000001", 2)
            + f"""
            DO $$ BEGIN
              IF (
                SELECT status FROM pre_invoices WHERE id = '{P1}'
              ) <> 'SUPERSEDED' THEN
                RAISE EXCEPTION 'old not superseded';
              END IF;

              IF (
                SELECT version FROM pre_invoices WHERE id = '{P2}'
              ) <> 2 THEN
                RAISE EXCEPTION 'version';
              END IF;
            END $$;
            """
        )

    def test_cancelling_a_draft_needs_no_reason_and_the_next_draft_is_allowed(self):
        self.ok(
            self.request(R1, "T-000001")
            + self.draft(P1, R1)
            + self.cancel(P1)
            + self.draft(P2, R1)
        )

    def test_pre_invoice_series_is_independent_of_the_request_series(self):
        out = self.ok(
            f"""
            SELECT next_request_number('{self.org}', 'PI-1405');
            SELECT next_request_number('{self.org}', 'PI-1405');
            SELECT next_request_number('{self.org}', 'PI-1406');
            """
        )

        self.assertEqual(
            [l for l in out.split() if l.isdigit()][-3:],
            ["1", "2", "1"],
        )

    # ------------------------------------------------------------------
    # Insert rules
    # ------------------------------------------------------------------

    def test_draft_only_and_submitted_source_only(self):
        self.state_conflict(
            self.request(R1, "T-000001"),
            self.draft(
                P1,
                R1,
                with_items=False,
                status="ISSUED",
            ).rstrip(";"),
        )

        self.state_conflict(
            self.request(R1, "T-000001", status="DRAFT"),
            self.draft(P1, R1, with_items=False).rstrip(";"),
        )

        self.state_conflict(
            self.request(R1, "T-000001", status="CANCELLED"),
            self.draft(P1, R1, with_items=False).rstrip(";"),
        )

    def test_replacement_rules(self):
        setup = self.issued() + self.request(R2, "T-000002")

        self.state_conflict(
            self.request(R1, "T-000001")
            + self.draft(P1, R1)
            + self.request(R2, "T-000002"),
            self.draft(
                P2,
                R2,
                chain=P1,
                supersedes=P1,
                with_items=False,
            ).rstrip(";"),
        )

        self.internal(
            setup,
            self.draft(
                P2,
                R1,
                chain=P1,
                supersedes=P1,
                with_items=False,
            ).rstrip(";"),
            "chk_pre_invoices_chain_consistency",
        )

        self.internal(
            setup,
            self.draft(
                P2,
                R2,
                chain=P2,
                supersedes=P1,
                with_items=False,
            ).rstrip(";"),
            "chk_pre_invoices_chain_consistency",
        )

    def test_a_document_is_replaced_once_and_issue_needs_the_predecessor_superseded(self):
        base = (
            self.issued()
            + self.request(R2, "T-000002")
            + self.draft(P2, R2, chain=P1, supersedes=P1)
        )

        self.state_conflict(
            base,
            self.issue(P2, "PI-1405-000001", 2).rstrip(";"),
        )

        self.fails(
            base
            + self.request(
                "00000000-0000-0000-0000-0000000000a3",
                "T-000003",
            ),
            self.draft(
                P3,
                "00000000-0000-0000-0000-0000000000a3",
                chain=P1,
                supersedes=P1,
                with_items=False,
            ).rstrip(";"),
            "23505",
            "uq_pre_invoices_one_successor",
        )

        self.internal(
            base + self.supersede(P1),
            self.issue(P2, "PI-1405-000001", 3).rstrip(";"),
            "chk_pre_invoices_version_chain",
        )

        self.internal(
            base + self.supersede(P1),
            self.issue(P2, "PI-1405-000009", 2).rstrip(";"),
            "chk_pre_invoices_version_chain",
        )

    def test_superseded_only_together_with_a_replacement(self):
        self.state_conflict(
            self.issued(),
            self.supersede(P1).rstrip(";"),
        )

    # ------------------------------------------------------------------
    # State machine and immutability
    # ------------------------------------------------------------------

    def test_illegal_transitions(self):
        issued = self.issued()

        self.state_conflict(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            self.supersede(P1).rstrip(";"),
        )

        self.state_conflict(
            issued,
            f"UPDATE pre_invoices SET status='DRAFT' WHERE id='{P1}'",
        )

        self.state_conflict(
            issued + self.cancel(P1, "'x'"),
            self.issue(P1, "PI-1405-000002").rstrip(";"),
        )

        self.state_conflict(
            issued + self.cancel(P1, "'x'"),
            f"UPDATE pre_invoices SET status='DRAFT' WHERE id='{P1}'",
        )

        self.state_conflict(
            self.request(R1, "T-000001")
            + self.draft(P1, R1)
            + self.cancel(P1),
            self.issue(P1).rstrip(";"),
        )

    def test_snapshot_and_identity_are_immutable_in_every_state(self):
        for setup in (
            self.request(R1, "T-000001") + self.draft(P1, R1),
            self.issued(),
        ):
            for col, val in [
                ("customer_name", "'X'"),
                ("seller_name", "'X'"),
                ("grand_total", "grand_total + 1"),
                ("request_number", "'X'"),
                ("customer_mobile", "'0999'"),
                ("request_notes", "'x'"),
            ]:
                self.state_conflict(
                    setup,
                    f"UPDATE pre_invoices SET {col} = {val} WHERE id='{P1}'",
                )

            self.state_conflict(
                setup,
                f"UPDATE pre_invoices "
                f"SET source_request_id = source_request_id, "
                f"chain_id = '{P2}' WHERE id='{P1}'",
            )

    def test_number_version_and_issue_data_are_frozen_after_issue(self):
        self.state_conflict(
            self.issued(),
            f"UPDATE pre_invoices "
            f"SET pre_invoice_number='PI-1405-000009' WHERE id='{P1}'",
        )

        self.state_conflict(
            self.issued(),
            f"UPDATE pre_invoices SET version=2 WHERE id='{P1}'",
        )

        self.state_conflict(
            self.issued(),
            f"UPDATE pre_invoices "
            f"SET issued_at = NOW() + interval '1 day' WHERE id='{P1}'",
        )

        self.state_conflict(
            self.issued() + self.cancel(P1, "'why'"),
            f"UPDATE pre_invoices "
            f"SET cancel_reason='other' WHERE id='{P1}'",
        )

    def test_no_deletes_anywhere(self):
        setup = self.issued()

        self.state_conflict(
            setup,
            f"DELETE FROM pre_invoices WHERE id='{P1}'",
        )

        self.state_conflict(
            setup,
            f"DELETE FROM pre_invoice_items WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            setup,
            f"DELETE FROM pre_invoice_documents WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            f"DELETE FROM pre_invoices WHERE id='{P1}'",
        )

    def test_items_are_written_once_while_draft(self):
        extra = (
            f"INSERT INTO pre_invoice_items "
            f"(organization_id, pre_invoice_id, line_no, product_id, "
            f"product_code, product_name, unit_id, unit_code, unit_name, "
            f"quantity, bundle_count, unit_price, discount_amount, "
            f"tax_amount, line_total) "
            f"VALUES ('{self.org}','{P1}',2,'{self.prod}','P','P',"
            f"'{self.unit}','KG','Kg',1,0,10,0,0,10)"
        )

        self.state_conflict(
            self.issued(),
            extra,
        )

        self.state_conflict(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            f"UPDATE pre_invoice_items "
            f"SET quantity = 2 WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            f"UPDATE pre_invoice_items "
            f"SET notes = 'x' WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            self.issued(),
            f"UPDATE pre_invoice_items "
            f"SET notes = 'x' WHERE pre_invoice_id='{P1}'",
        )

    # ------------------------------------------------------------------
    # Consistency checks
    # ------------------------------------------------------------------

    def test_issue_requires_items_matching_the_header(self):
        self.internal(
            self.request(R1, "T-000001")
            + self.draft(P1, R1, with_items=False),
            self.issue(P1).rstrip(";"),
            "chk_pre_invoices_items_consistency",
        )

    def test_issue_data_must_be_complete(self):
        self.fails(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            f"UPDATE pre_invoices "
            f"SET status='ISSUED', issued_by='{self.admin}', "
            f"issued_at=NOW() WHERE id='{P1}'",
            "23514",
        )

        self.fails(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            f"UPDATE pre_invoices "
            f"SET status='ISSUED', "
            f"pre_invoice_number='PI-1405-000001', "
            f"version=1 WHERE id='{P1}'",
            "23514",
        )

    def test_issue_needs_a_submitted_source_request(self):
        setup = (
            self.request(R1, "T-000001")
            + self.draft(P1, R1)
        )

        self.state_conflict(
            setup
            + f"UPDATE purchase_requests "
            f"SET status='CANCELLED' WHERE id='{R1}';",
            self.issue(P1).rstrip(";"),
        )

    def test_money_and_cancel_reason_constraints(self):
        bad_total = (
            self.draft(P1, R1, with_items=False)
            .replace(
                "r.grand_total,",
                "r.grand_total + 1,",
                1,
            )
            .rstrip(";")
        )

        self.fails(
            self.request(R1, "T-000001"),
            bad_total,
            "23514",
            "chk_pre_invoices_grand_total_consistency",
        )

        self.fails(
            self.issued(),
            self.cancel(P1).rstrip(";"),
            "23514",
            "chk_pre_invoices_status_shape",
        )

        base = self.issued()

        self.fails(
            base,
            self.cancel(P1, "' padded '").rstrip(";"),
            "23514",
            "chk_pre_invoices_cancel_reason",
        )

        self.fails(
            base,
            self.cancel(P1, "repeat('x', 501)").rstrip(";"),
            "23514",
            "chk_pre_invoices_cancel_reason",
        )

        self.fails(
            base,
            self.cancel(P1, "''").rstrip(";"),
            "23514",
            "chk_pre_invoices_cancel_reason",
        )

        self.ok(
            base + self.cancel(P1, "repeat('x', 500)")
        )

    def test_item_line_total_must_be_consistent(self):
        bad = (
            f"INSERT INTO pre_invoice_items "
            f"(organization_id, pre_invoice_id, line_no, product_id, "
            f"product_code, product_name, unit_id, unit_code, unit_name, "
            f"quantity, bundle_count, unit_price, discount_amount, "
            f"tax_amount, line_total) "
            f"VALUES ('{self.org}','{P1}',2,'{self.prod}','P','P',"
            f"'{self.unit}','KG','Kg',1,0,10,0,0,11)"
        )

        self.fails(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            bad,
            "23514",
            "chk_pre_invoice_items_line_total_consistency",
        )

    # ------------------------------------------------------------------
    # One active document
    # ------------------------------------------------------------------

    def test_one_draft_per_request(self):
        self.fails(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            self.draft(P2, R1, with_items=False).rstrip(";"),
            "23505",
            "uq_pre_invoices_draft_per_request",
        )

    def test_one_issued_per_request_even_if_the_service_were_bypassed(self):
        setup = (
            self.issued()
            + self.draft(P2, R1)
        )

        e = self.fails(
            setup,
            self.issue(P2, "PI-1405-000002").rstrip(";"),
            "23505",
            "uq_pre_invoices_issued_per_request",
        )

        self.assertEqual(
            (e.code, e.details),
            (
                ErrorCode.DUPLICATE_RESOURCE,
                {"reason": "ACTIVE_PRE_INVOICE_EXISTS"},
            ),
        )

    def test_number_unique_per_organization_for_version_one(self):
        setup = (
            self.issued()
            + self.request(R2, "T-000002")
            + self.draft(P2, R2)
        )

        self.fails(
            setup,
            self.issue(P2, "PI-1405-000001").rstrip(";"),
            "23505",
        )

    # ------------------------------------------------------------------
    # Organization isolation
    # ------------------------------------------------------------------

    def test_cross_organization_references_are_refused_by_composite_foreign_keys(self):
        other = (
            "INSERT INTO organizations (id, code, name) "
            "VALUES ('00000000-0000-0000-0000-0000000000f1','XORG','X Org');"
            "INSERT INTO customers "
            "(id, organization_id, customer_type, name) "
            "VALUES "
            "('00000000-0000-0000-0000-0000000000f2',"
            "'00000000-0000-0000-0000-0000000000f1',"
            "'COMPANY','X customer');"
        )

        sql = (
            self.draft(P1, R1, with_items=False)
            .replace(
                "c.id,",
                "'00000000-0000-0000-0000-0000000000f2',",
                1,
            )
            .rstrip(";")
        )

        e = self.fails(
            self.request(R1, "T-000001") + other,
            sql,
            "23503",
        )

        self.assertEqual(
            e.code,
            ErrorCode.REFERENCE_NOT_FOUND,
        )

        sql = (
            self.draft(P1, R1, with_items=False)
            .replace(
                "r.organization_id,",
                "'00000000-0000-0000-0000-0000000000f1',",
                1,
            )
            .rstrip(";")
        )

        self.state_conflict(
            self.request(R1, "T-000001") + other,
            sql,
        )

    # ------------------------------------------------------------------
    # Documents (PDF)
    # ------------------------------------------------------------------

    def test_document_only_for_an_issued_pre_invoice_and_starts_pending(self):
        self.state_conflict(
            self.request(R1, "T-000001") + self.draft(P1, R1),
            self.document(P1).rstrip(";"),
        )

        self.state_conflict(
            self.issued().replace(self.document(P1), ""),
            f"INSERT INTO pre_invoice_documents "
            f"(organization_id, pre_invoice_id, attempt_count) "
            f"VALUES ('{self.org}','{P1}',1)",
        )

        self.fails(
            self.issued(),
            self.document(P1).rstrip(";"),
            "23505",
            "uq_pre_invoice_documents_pre_invoice",
        )

    def ready(self, data=PDF):
        return (
            f"UPDATE pre_invoice_documents "
            f"SET status='ready', pdf_data={data}, "
            f"pdf_size=octet_length({data}), "
            f"pdf_sha256=encode(sha256({data}),'hex'), "
            f"template_version='pi-1', generated_at=NOW(), "
            f"last_error_code=NULL "
            f"WHERE pre_invoice_id='{P1}';"
        )

    def test_pdf_integrity_constraints(self):
        base = self.issued()

        self.fails(
            base,
            self.ready(
                "convert_to('<html>no</html>', 'UTF8')"
            ).rstrip(";"),
            "23514",
            "chk_pre_invoice_documents_integrity",
        )

        self.fails(
            base,
            f"UPDATE pre_invoice_documents "
            f"SET status='ready', pdf_data={PDF}, pdf_size=1, "
            f"pdf_sha256=encode(sha256({PDF}),'hex'), "
            f"template_version='pi-1', generated_at=NOW() "
            f"WHERE pre_invoice_id='{P1}'",
            "23514",
            "chk_pre_invoice_documents_integrity",
        )

        self.fails(
            base,
            f"UPDATE pre_invoice_documents "
            f"SET status='ready', pdf_data={PDF}, "
            f"pdf_size=octet_length({PDF}), "
            f"pdf_sha256=repeat('0',64), "
            f"template_version='pi-1', generated_at=NOW() "
            f"WHERE pre_invoice_id='{P1}'",
            "23514",
            "chk_pre_invoice_documents_integrity",
        )

        self.fails(
            base,
            f"UPDATE pre_invoice_documents "
            f"SET status='ready' WHERE pre_invoice_id='{P1}'",
            "23514",
            "chk_pre_invoice_documents_shape",
        )

        self.fails(
            base,
            f"UPDATE pre_invoice_documents "
            f"SET status='failed' WHERE pre_invoice_id='{P1}'",
            "23514",
            "chk_pre_invoice_documents_shape",
        )

        self.fails(
            base,
            f"UPDATE pre_invoice_documents "
            f"SET status='failed', last_error_code='lower case' "
            f"WHERE pre_invoice_id='{P1}'",
            "23514",
        )

    def test_pdf_size_cap_is_ten_mebibytes(self):
        big = (
            "(convert_to('%PDF-', 'UTF8') || "
            "convert_to(repeat('x', 10485757), 'UTF8'))"
        )

        self.fails(
            self.issued(),
            self.ready(big).rstrip(";"),
            "23514",
            "chk_pre_invoice_documents_size",
        )

        exact = (
            "(convert_to('%PDF-', 'UTF8') || "
            "convert_to(repeat('x', 10485755), 'UTF8'))"
        )

        self.ok(
            self.issued() + self.ready(exact)
        )

    def test_a_ready_document_and_its_hash_are_final(self):
        ready = self.issued() + self.ready()

        self.state_conflict(
            ready,
            f"UPDATE pre_invoice_documents "
            f"SET pdf_sha256 = repeat('a', 64) "
            f"WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            ready,
            f"UPDATE pre_invoice_documents "
            f"SET status='failed', "
            f"last_error_code='PDF_RENDER_FAILED', "
            f"pdf_data=NULL, pdf_sha256=NULL, pdf_size=NULL, "
            f"generated_at=NULL WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            ready,
            f"UPDATE pre_invoice_documents "
            f"SET attempt_count = attempt_count + 1 "
            f"WHERE pre_invoice_id='{P1}'",
        )

    def test_attempt_counters_and_failed_pending(self):
        failed = (
            self.issued()
            + f"UPDATE pre_invoice_documents "
            f"SET status='failed', "
            f"last_error_code='PDF_RENDER_FAILED', "
            f"attempt_count=2, window_attempt_count=1 "
            f"WHERE pre_invoice_id='{P1}';"
        )

        self.state_conflict(
            failed,
            f"UPDATE pre_invoice_documents "
            f"SET attempt_count = 1 WHERE pre_invoice_id='{P1}'",
        )

        self.state_conflict(
            failed,
            f"UPDATE pre_invoice_documents "
            f"SET status='pending', last_error_code=NULL "
            f"WHERE pre_invoice_id='{P1}'",
        )

        self.fails(
            failed,
            f"UPDATE pre_invoice_documents "
            f"SET window_attempt_count = 3 "
            f"WHERE pre_invoice_id='{P1}'",
            "23514",
            "chk_pre_invoice_documents_attempts",
        )

        self.ok(
            failed + self.ready()
        )

    def test_commercial_status_is_independent_of_the_document(self):
        self.ok(
            self.issued()
            + f"UPDATE pre_invoice_documents "
              f"SET status='failed', "
              f"last_error_code='PDF_RENDER_FAILED' "
              f"WHERE pre_invoice_id='{P1}';"
            + self.cancel(P1, "'withdrawn'")
            + f"""
            DO $$ BEGIN
              IF (
                SELECT status
                FROM pre_invoice_documents
                WHERE pre_invoice_id = '{P1}'
              ) <> 'failed' THEN
                RAISE EXCEPTION 'x';
              END IF;
            END $$;
            """
        )

    # ------------------------------------------------------------------
    # H2: cancellation metadata constraints
    # ------------------------------------------------------------------

    def test_cancel_reason_is_rejected_on_draft_insert(self):
        setup = self.request(R1, "T-000001")

        draft_sql = self.draft(
            P1,
            R1,
            with_items=False,
        ).rstrip(";")

        old_columns = "grand_total, created_by) SELECT"
        new_columns = "grand_total, created_by, cancel_reason) SELECT"

        old_values = "r.grand_total, r.created_by FROM"
        new_values = "r.grand_total, r.created_by, 'unexpected' FROM"

        self.assertIn(old_columns, draft_sql)
        self.assertIn(old_values, draft_sql)

        bad_draft = draft_sql.replace(
            old_columns,
            new_columns,
            1,
        ).replace(
            old_values,
            new_values,
            1,
        )

        self.assertNotEqual(bad_draft, draft_sql)
        self.state_conflict(setup, bad_draft)

    def test_cancel_reason_is_rejected_when_issuing_a_draft(self):
        setup = (
            self.request(R1, "T-000001")
            + self.draft(P1, R1)
        )

        failing = (
            f"UPDATE pre_invoices "
            f"SET status='ISSUED', cancel_reason='unexpected' "
            f"WHERE id='{P1}'"
        )

        self.state_conflict(setup, failing)

    def test_cancelled_by_is_rejected_when_superseding_an_issued_invoice(self):
        setup = (
            self.issued()
            + self.request(R2, "T-000002")
            + self.draft(P2, R2, chain=P1, supersedes=P1)
        )

        failing = (
            f"UPDATE pre_invoices "
            f"SET status='SUPERSEDED', superseded_at=NOW(), "
            f"cancelled_by='{self.admin}' "
            f"WHERE id='{P1}'"
        )

        self.state_conflict(setup, failing)

    # ------------------------------------------------------------------
    # H7: prevent cancellation of a request with an issued pre-invoice
    # ------------------------------------------------------------------

    def test_request_cancel_rejected_when_issued_pre_invoice_exists(self):
        self.state_conflict(
            self.issued(),
            f"UPDATE purchase_requests "
            f"SET status='CANCELLED' WHERE id='{R1}'",
        )


if __name__ == "__main__":
    unittest.main()