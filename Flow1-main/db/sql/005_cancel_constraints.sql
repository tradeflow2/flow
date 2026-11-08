-- TradeFlow
-- Migration: 005_cancel_constraints
-- Hardening: H2
-- Purpose: Prevent cancelled_by and cancel_reason from being populated unless the pre-invoice status is CANCELLED.
-- Migration 004 is immutable and must not be modified. This migration is forward-only.

-- 1. CHECK constraint
ALTER TABLE pre_invoices
ADD CONSTRAINT chk_pre_invoices_cancel_fields_state
CHECK (
    status = 'CANCELLED'
    OR (
        cancelled_by IS NULL
        AND cancel_reason IS NULL
    )
);

-- 2. Guard function
CREATE OR REPLACE FUNCTION trg_pre_invoices_cancel_fields_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status <> 'CANCELLED'
       AND (NEW.cancelled_by IS NOT NULL OR NEW.cancel_reason IS NOT NULL)
    THEN
        RAISE EXCEPTION
            'cancelled_by and cancel_reason are only allowed for CANCELLED pre-invoices'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Trigger
CREATE TRIGGER trg_pre_invoices_cancel_fields_guard
BEFORE INSERT OR UPDATE ON pre_invoices
FOR EACH ROW
EXECUTE FUNCTION trg_pre_invoices_cancel_fields_guard();
