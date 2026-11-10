CREATE OR REPLACE FUNCTION trg_purchase_requests_cancel_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'CANCELLED'
       AND OLD.status IS DISTINCT FROM NEW.status
    THEN
        IF EXISTS (
            SELECT 1
            FROM pre_invoices
            WHERE source_request_id = NEW.id
              AND organization_id = NEW.organization_id
              AND status = 'ISSUED'
        ) THEN
            RAISE EXCEPTION
                'Cannot cancel purchase request % because an issued pre-invoice exists.',
                NEW.id
                USING ERRCODE = '55000';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_purchase_requests_cancel_guard
    ON purchase_requests;

CREATE TRIGGER trg_purchase_requests_cancel_guard
BEFORE UPDATE OF status ON purchase_requests
FOR EACH ROW
EXECUTE FUNCTION trg_purchase_requests_cancel_guard();
