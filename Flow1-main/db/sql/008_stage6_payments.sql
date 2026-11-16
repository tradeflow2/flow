-- TradeFlow
-- Migration: 008_stage6_payments
-- Stage 6 — S6-A: Payments
-- Related: ADR-065, ADR-058, ADR-012, ADR-014, ADR-024
--
-- This migration creates:
--   1. payments table (payment registration for Pre-Invoices)
--   2. Constraints (method-specific, status-specific, date-specific)
--   3. Indexes
--   4. Triggers (no delete, frozen after confirmation)
--
-- Out of scope (S6-B, S6-C):
--   - Financial confirmation workflow
--   - Cheque lifecycle transitions beyond RECEIVED
--   - Credit agreements and credit allocation
--
-- Protected files (must not be modified):
--   001_initial_mvp.sql, 002_harden_mvp.sql, 004_pre_invoices.sql,
--   005_cancel_constraints.sql, 006_request_cancel_guard.sql, runner.py
--
-- Forward-only migration.

-- ---------------------------------------------------------------------------
-- 1. payments table
-- ---------------------------------------------------------------------------
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    pre_invoice_id UUID NOT NULL,
    payment_method VARCHAR(20) NOT NULL,
    amount NUMERIC(20,0) NOT NULL,
    payment_date DATE NOT NULL,
    reference_number VARCHAR(100),
    payer_name VARCHAR(255),
    bank_name VARCHAR(100),
    cheque_number VARCHAR(50),
    cheque_due_date DATE,
    cheque_status VARCHAR(20),
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_CONFIRMATION',
    notes TEXT,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_by UUID,
    confirmed_at TIMESTAMPTZ,
    reversed_by UUID,
    reversed_at TIMESTAMPTZ,
    reversal_reason TEXT,

    CONSTRAINT uq_payments_id_organization UNIQUE (id, organization_id),

    CONSTRAINT chk_payments_method CHECK (
        payment_method IN ('CASH', 'BANK_TRANSFER', 'CHEQUE')
    ),
    CONSTRAINT chk_payments_status CHECK (
        status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'REJECTED', 'REVERSED')
    ),
    CONSTRAINT chk_payments_amount CHECK (amount > 0),
    CONSTRAINT chk_payments_cheque_status CHECK (
        cheque_status IS NULL
        OR cheque_status IN ('RECEIVED', 'DEPOSITED', 'CLEARED', 'BOUNCED')
    ),

    -- Method-specific field validation
    CONSTRAINT chk_payments_method_fields CHECK (
        (payment_method = 'CASH'
            AND cheque_number IS NULL
            AND cheque_due_date IS NULL
            AND cheque_status IS NULL)
        OR (payment_method = 'BANK_TRANSFER'
            AND cheque_number IS NULL
            AND cheque_due_date IS NULL
            AND cheque_status IS NULL)
        OR (payment_method = 'CHEQUE'
            AND cheque_number IS NOT NULL
            AND cheque_due_date IS NOT NULL
            AND cheque_status IS NOT NULL)
    ),

    -- Status-specific field validation
    CONSTRAINT chk_payments_status_fields CHECK (
        (status = 'PENDING_CONFIRMATION'
            AND confirmed_by IS NULL
            AND confirmed_at IS NULL
            AND reversed_by IS NULL
            AND reversed_at IS NULL)
        OR (status = 'CONFIRMED'
            AND confirmed_by IS NOT NULL
            AND confirmed_at IS NOT NULL
            AND reversed_by IS NULL
            AND reversed_at IS NULL)
        OR (status = 'REJECTED'
            AND confirmed_by IS NOT NULL
            AND confirmed_at IS NOT NULL
            AND reversed_by IS NULL
            AND reversed_at IS NULL)
        OR (status = 'REVERSED'
            AND reversed_by IS NOT NULL
            AND reversed_at IS NOT NULL
            AND reversal_reason IS NOT NULL)
    ),

    -- Date validation
    CONSTRAINT chk_payments_payment_date CHECK (
        payment_date <= CURRENT_DATE + INTERVAL '1 day'
    ),
    CONSTRAINT chk_payments_cheque_due_date CHECK (
        cheque_due_date IS NULL OR cheque_due_date >= payment_date
    ),

    -- Foreign keys (composite, organization-scoped)
    CONSTRAINT fk_payments_pre_invoice
        FOREIGN KEY (pre_invoice_id, organization_id)
        REFERENCES pre_invoices (id, organization_id),
    CONSTRAINT fk_payments_created_by
        FOREIGN KEY (created_by, organization_id)
        REFERENCES users (id, organization_id),
    CONSTRAINT fk_payments_confirmed_by
        FOREIGN KEY (confirmed_by, organization_id)
        REFERENCES users (id, organization_id),
    CONSTRAINT fk_payments_reversed_by
        FOREIGN KEY (reversed_by, organization_id)
        REFERENCES users (id, organization_id)
);

-- ---------------------------------------------------------------------------
-- 2. Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_payments_pre_invoice
    ON payments (organization_id, pre_invoice_id);
CREATE INDEX idx_payments_status
    ON payments (organization_id, status);
CREATE INDEX idx_payments_created_by
    ON payments (organization_id, created_by);

-- ---------------------------------------------------------------------------
-- 3. Trigger: prevent DELETE (payments are never physically deleted)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_payments_no_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'payments cannot be deleted' USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_payments_no_delete
BEFORE DELETE ON payments
FOR EACH ROW EXECUTE FUNCTION trg_payments_no_delete();

-- ---------------------------------------------------------------------------
-- 4. Trigger: prevent UPDATE on CONFIRMED or REVERSED payments
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_payments_frozen() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('CONFIRMED', 'REVERSED') THEN
        RAISE EXCEPTION 'payments with status % are frozen', OLD.status
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_payments_frozen
BEFORE UPDATE ON payments
FOR EACH ROW EXECUTE FUNCTION trg_payments_frozen();