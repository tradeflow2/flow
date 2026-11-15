-- TradeFlow Stage 5: commercial snapshots, discount policy and approval integrity.
-- Protected migrations 001..006 are intentionally not modified.

-- 1) Request commercial controls (request-level discount is separate from legacy header totals).
ALTER TABLE purchase_requests
    ADD COLUMN IF NOT EXISTS request_discount_amount NUMERIC(20,0) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS discount_applied_by UUID;
ALTER TABLE purchase_request_items ADD COLUMN discount_applied_by UUID;
UPDATE purchase_request_items i SET discount_applied_by=r.created_by
  FROM purchase_requests r WHERE r.id=i.purchase_request_id AND r.organization_id=i.organization_id
    AND i.discount_amount>0 AND i.discount_applied_by IS NULL;
ALTER TABLE purchase_request_items ADD CONSTRAINT chk_request_item_discount_actor
  CHECK ((discount_amount=0 AND discount_applied_by IS NULL) OR (discount_amount>0 AND discount_applied_by IS NOT NULL));
ALTER TABLE purchase_request_items ADD CONSTRAINT fk_request_item_discount_applied_by FOREIGN KEY (discount_applied_by, organization_id) REFERENCES users(id, organization_id);
ALTER TABLE purchase_requests
    ADD CONSTRAINT chk_request_discount_amount_nonnegative CHECK (request_discount_amount >= 0);
ALTER TABLE purchase_requests
    ADD CONSTRAINT fk_request_discount_applied_by FOREIGN KEY (discount_applied_by, organization_id)
      REFERENCES users (id, organization_id);
ALTER TABLE purchase_requests ADD CONSTRAINT chk_request_discount_actor
  CHECK ((request_discount_amount=0 AND discount_applied_by IS NULL) OR (request_discount_amount>0 AND discount_applied_by IS NOT NULL));

-- 2) Policies are versioned records. Parameter changes create a new row/version.
CREATE TABLE discount_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    scope_type VARCHAR(20) NOT NULL CHECK (scope_type IN ('ORGANIZATION','PRODUCT')),
    scope_id UUID,
    role_code VARCHAR(50) NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    direct_limit_amount NUMERIC(20,0) NOT NULL DEFAULT 0 CHECK (direct_limit_amount >= 0),
    direct_limit_percent NUMERIC(9,4) NOT NULL DEFAULT 0 CHECK (direct_limit_percent BETWEEN 0 AND 100),
    approver_limit_amount NUMERIC(20,0) NOT NULL DEFAULT 0 CHECK (approver_limit_amount >= 0),
    approver_limit_percent NUMERIC(9,4) NOT NULL DEFAULT 0 CHECK (approver_limit_percent BETWEEN 0 AND 100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deactivated_by UUID,
    deactivated_at TIMESTAMPTZ,
    CONSTRAINT uq_discount_policy_version UNIQUE (organization_id, scope_type, scope_id, role_code, version),
    CONSTRAINT uq_discount_policy_id_org UNIQUE (id, organization_id),
    CONSTRAINT fk_discount_policy_product FOREIGN KEY (scope_id, organization_id) REFERENCES products(id, organization_id),
    CONSTRAINT fk_discount_policy_created_by FOREIGN KEY (created_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT fk_discount_policy_deactivated_by FOREIGN KEY (deactivated_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT chk_discount_policy_scope CHECK ((scope_type='ORGANIZATION' AND scope_id IS NULL) OR (scope_type='PRODUCT' AND scope_id IS NOT NULL)),
    CONSTRAINT chk_discount_policy_deactivation CHECK ((is_active AND deactivated_at IS NULL AND deactivated_by IS NULL) OR (NOT is_active AND deactivated_at IS NOT NULL AND deactivated_by IS NOT NULL))
);
CREATE UNIQUE INDEX uq_discount_policy_version_key ON discount_policies
    (organization_id, scope_type, COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid), role_code, version);
CREATE UNIQUE INDEX uq_discount_policy_active_scope ON discount_policies
    (organization_id, scope_type, COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid), role_code)
    WHERE is_active;
CREATE INDEX idx_discount_policy_lookup ON discount_policies(organization_id, role_code, scope_type, is_active);

-- 3) One mutable version/dirty marker per request. Snapshot rows themselves are immutable.
CREATE TABLE request_commercial_state (
    organization_id UUID NOT NULL,
    request_id UUID NOT NULL,
    current_version BIGINT NOT NULL DEFAULT 0 CHECK (current_version >= 0),
    is_dirty BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, request_id),
    CONSTRAINT fk_request_commercial_state_request FOREIGN KEY (request_id, organization_id)
      REFERENCES purchase_requests(id, organization_id) ON DELETE CASCADE
);

CREATE TABLE commercial_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    request_id UUID NOT NULL,
    commercial_version BIGINT NOT NULL CHECK (commercial_version >= 1),
    customer_id UUID NOT NULL,
    customer_snapshot JSONB NOT NULL,
    request_number VARCHAR(50) NOT NULL,
    request_date TIMESTAMPTZ NOT NULL,
    requested_delivery_date TIMESTAMPTZ,
    request_notes TEXT,
    seller_snapshot JSONB NOT NULL,
    subtotal NUMERIC(20,0) NOT NULL CHECK (subtotal >= 0),
    item_discount_total NUMERIC(20,0) NOT NULL CHECK (item_discount_total >= 0),
    request_discount_amount NUMERIC(20,0) NOT NULL CHECK (request_discount_amount >= 0),
    request_discount_applied_by UUID,
    discount_total NUMERIC(20,0) NOT NULL CHECK (discount_total >= 0),
    tax_total NUMERIC(20,0) NOT NULL CHECK (tax_total >= 0),
    grand_total NUMERIC(20,0) NOT NULL CHECK (grand_total >= 0),
    approval_required BOOLEAN NOT NULL,
    policy_snapshot JSONB NOT NULL,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_commercial_snapshot_id_org UNIQUE (id, organization_id),
    CONSTRAINT uq_commercial_snapshot_request_version UNIQUE (organization_id, request_id, commercial_version),
    CONSTRAINT fk_commercial_snapshot_request FOREIGN KEY (request_id, organization_id) REFERENCES purchase_requests(id, organization_id),
    CONSTRAINT fk_commercial_snapshot_request_discount_applier FOREIGN KEY (request_discount_applied_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT fk_commercial_snapshot_customer FOREIGN KEY (customer_id, organization_id) REFERENCES customers(id, organization_id),
    CONSTRAINT fk_commercial_snapshot_creator FOREIGN KEY (created_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT chk_commercial_snapshot_json CHECK (jsonb_typeof(customer_snapshot)='object' AND jsonb_typeof(seller_snapshot)='object' AND jsonb_typeof(policy_snapshot)='object'),
    CONSTRAINT chk_commercial_snapshot_request_discount_actor CHECK ((request_discount_amount=0 AND request_discount_applied_by IS NULL) OR (request_discount_amount>0 AND request_discount_applied_by IS NOT NULL)),
    CONSTRAINT chk_commercial_snapshot_total CHECK (grand_total = subtotal - discount_total + tax_total),
    CONSTRAINT chk_commercial_snapshot_discount_sum CHECK (discount_total = item_discount_total + request_discount_amount)
);
CREATE INDEX idx_commercial_snapshots_request ON commercial_snapshots(organization_id, request_id, commercial_version DESC);

CREATE TABLE commercial_snapshot_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    line_no INTEGER NOT NULL CHECK (line_no >= 1),
    source_item_id UUID NOT NULL,
    product_id UUID NOT NULL,
    product_code VARCHAR(100) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    unit_id UUID NOT NULL,
    unit_code VARCHAR(50) NOT NULL,
    unit_name VARCHAR(100) NOT NULL,
    quantity NUMERIC(18,3) NOT NULL CHECK (quantity > 0),
    bundle_count NUMERIC(18,0) NOT NULL DEFAULT 0 CHECK (bundle_count >= 0),
    estimated_weight NUMERIC(18,3),
    unit_price NUMERIC(20,0) NOT NULL CHECK (unit_price >= 0),
    gross_amount NUMERIC(20,0) NOT NULL CHECK (gross_amount >= 0),
    item_discount_amount NUMERIC(20,0) NOT NULL CHECK (item_discount_amount >= 0),
    discount_applied_by UUID,
    request_discount_allocation NUMERIC(20,0) NOT NULL CHECK (request_discount_allocation >= 0),
    net_amount NUMERIC(20,0) NOT NULL CHECK (net_amount >= 0),
    tax_amount NUMERIC(20,0) NOT NULL CHECK (tax_amount >= 0),
    line_total NUMERIC(20,0) NOT NULL CHECK (line_total >= 0),
    notes TEXT,
    specifications JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_commercial_snapshot_item_line UNIQUE(snapshot_id, line_no),
    CONSTRAINT fk_commercial_snapshot_item_discount_applied_by FOREIGN KEY(discount_applied_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT fk_commercial_snapshot_item_snapshot FOREIGN KEY(snapshot_id, organization_id)
      REFERENCES commercial_snapshots(id, organization_id) ON DELETE RESTRICT,
    CONSTRAINT fk_commercial_snapshot_item_source FOREIGN KEY(source_item_id, organization_id)
      REFERENCES purchase_request_items(id, organization_id),
    CONSTRAINT fk_commercial_snapshot_item_product FOREIGN KEY(product_id, organization_id)
      REFERENCES products(id, organization_id),
    CONSTRAINT fk_commercial_snapshot_item_unit FOREIGN KEY(unit_id, organization_id)
      REFERENCES units(id, organization_id),
    CONSTRAINT chk_commercial_snapshot_item_discount_actor CHECK ((item_discount_amount=0 AND discount_applied_by IS NULL) OR (item_discount_amount>0 AND discount_applied_by IS NOT NULL)),
    CONSTRAINT chk_commercial_snapshot_item_specs CHECK (jsonb_typeof(specifications) = 'array' AND jsonb_typeof(policy_snapshot) = 'object'),
    CONSTRAINT chk_commercial_snapshot_item_math CHECK (
      gross_amount = ROUND(quantity * unit_price, 0)
      AND item_discount_amount <= gross_amount
      AND request_discount_allocation <= gross_amount - item_discount_amount
      AND net_amount = gross_amount - item_discount_amount - request_discount_allocation
      AND line_total = net_amount + tax_amount
    )
);
CREATE INDEX idx_commercial_snapshot_items_snapshot ON commercial_snapshot_items(organization_id, snapshot_id, line_no);

CREATE TABLE discount_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    request_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    commercial_version BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL CHECK(status IN ('DRAFT','APPROVED','REJECTED','INVALIDATED')),
    requested_by UUID NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    decided_by UUID,
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    rejected_txid BIGINT,
    invalidated_by UUID,
    invalidated_at TIMESTAMPTZ,
    CONSTRAINT uq_discount_approval_id_org UNIQUE(id, organization_id),
    CONSTRAINT uq_discount_approval_snapshot UNIQUE(organization_id, snapshot_id),
    CONSTRAINT fk_discount_approval_request FOREIGN KEY(request_id, organization_id) REFERENCES purchase_requests(id, organization_id),
    CONSTRAINT fk_discount_approval_snapshot FOREIGN KEY(snapshot_id, organization_id) REFERENCES commercial_snapshots(id, organization_id),
    CONSTRAINT fk_discount_approval_requester FOREIGN KEY(requested_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT fk_discount_approval_decider FOREIGN KEY(decided_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT fk_discount_approval_invalidator FOREIGN KEY(invalidated_by, organization_id) REFERENCES users(id, organization_id),
    CONSTRAINT chk_discount_approval_decision_shape CHECK (
      (status='DRAFT' AND decided_by IS NULL AND decided_at IS NULL AND rejected_txid IS NULL AND invalidated_by IS NULL AND invalidated_at IS NULL)
      OR (status='APPROVED' AND decided_by IS NOT NULL AND decided_at IS NOT NULL AND rejected_txid IS NULL AND invalidated_by IS NULL AND invalidated_at IS NULL)
      OR (status='REJECTED' AND decided_by IS NOT NULL AND decided_at IS NOT NULL AND rejected_txid IS NOT NULL AND invalidated_by IS NULL AND invalidated_at IS NULL AND decision_reason IS NOT NULL AND char_length(btrim(decision_reason)) BETWEEN 1 AND 500)
      OR (status='INVALIDATED' AND invalidated_by IS NOT NULL AND invalidated_at IS NOT NULL)
    )
);
CREATE INDEX idx_discount_approvals_request ON discount_approvals(organization_id, request_id, requested_at DESC);

CREATE TABLE discount_approval_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id UUID NOT NULL,
    approval_id UUID NOT NULL,
    request_id UUID NOT NULL,
    event_type VARCHAR(20) NOT NULL CHECK(event_type IN ('REQUESTED','APPROVED','REJECTED','INVALIDATED')),
    actor_id UUID NOT NULL,
    reason TEXT,
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_discount_history_approval FOREIGN KEY(approval_id, organization_id) REFERENCES discount_approvals(id, organization_id),
    CONSTRAINT fk_discount_history_request FOREIGN KEY(request_id, organization_id) REFERENCES purchase_requests(id, organization_id),
    CONSTRAINT fk_discount_history_actor FOREIGN KEY(actor_id, organization_id) REFERENCES users(id, organization_id)
);
CREATE INDEX idx_discount_history_approval ON discount_approval_history(organization_id, approval_id, id);

-- Required target key for the organization-safe Pre-Invoice link FK.
CREATE UNIQUE INDEX uq_pre_invoices_id_org_source ON pre_invoices(id, organization_id, source_request_id);

CREATE TABLE pre_invoice_commercial_links (
    organization_id UUID NOT NULL,
    pre_invoice_id UUID NOT NULL,
    source_request_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    approval_id UUID,
    commercial_version BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(organization_id, pre_invoice_id),
    CONSTRAINT fk_pi_link_preinvoice FOREIGN KEY(pre_invoice_id, organization_id, source_request_id)
      REFERENCES pre_invoices(id, organization_id, source_request_id),
    CONSTRAINT fk_pi_link_snapshot FOREIGN KEY(snapshot_id, organization_id)
      REFERENCES commercial_snapshots(id, organization_id),
    CONSTRAINT fk_pi_link_approval FOREIGN KEY(approval_id, organization_id)
      REFERENCES discount_approvals(id, organization_id),
    CONSTRAINT fk_pi_link_request FOREIGN KEY(source_request_id, organization_id)
      REFERENCES purchase_requests(id, organization_id)
);
CREATE INDEX idx_pi_link_snapshot ON pre_invoice_commercial_links(organization_id, snapshot_id);

-- 8th table: append-only audit of discount inputs/calculation, distinct from decision history.
CREATE TABLE discount_application_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id UUID NOT NULL,
    request_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    event_type VARCHAR(30) NOT NULL CHECK(event_type IN ('DISCOUNT_SET','DISCOUNT_CLEARED')),
    amount NUMERIC(20,0) NOT NULL DEFAULT 0 CHECK(amount >= 0),
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_discount_event_request FOREIGN KEY(request_id, organization_id) REFERENCES purchase_requests(id, organization_id),
    CONSTRAINT fk_discount_event_actor FOREIGN KEY(actor_id, organization_id) REFERENCES users(id, organization_id)
);

-- The triple key is required by the organization-safe link FK. Existing 004 is not edited.

-- Every request gets its state row, including requests created after this migration.
INSERT INTO request_commercial_state(organization_id, request_id)
SELECT organization_id, id FROM purchase_requests ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION trg_stage5_request_state_create() RETURNS trigger AS $$
BEGIN
  INSERT INTO request_commercial_state(organization_id, request_id, current_version, is_dirty)
  VALUES(NEW.organization_id, NEW.id, 0, TRUE) ON CONFLICT DO NOTHING;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_request_state_create AFTER INSERT ON purchase_requests
FOR EACH ROW EXECUTE FUNCTION trg_stage5_request_state_create();

-- Dirty marking is deliberately the only database-side reaction to commercial edits.
CREATE OR REPLACE FUNCTION trg_stage5_mark_request_dirty() RETURNS trigger AS $$
DECLARE v_org UUID; v_req UUID;
BEGIN
  IF TG_TABLE_NAME='purchase_requests' THEN
    v_org := NEW.organization_id; v_req := NEW.id;
    IF (NEW.customer_id, NEW.requested_delivery_date, NEW.request_discount_amount, NEW.discount_applied_by)
       IS DISTINCT FROM (OLD.customer_id, OLD.requested_delivery_date, OLD.request_discount_amount, OLD.discount_applied_by) THEN
      UPDATE request_commercial_state SET is_dirty=TRUE, updated_at=clock_timestamp()
       WHERE organization_id=v_org AND request_id=v_req;
    END IF;
  ELSE
    IF TG_OP='UPDATE' THEN
      IF (NEW.product_id,NEW.quantity,NEW.unit_id,NEW.bundle_count,NEW.estimated_weight,NEW.unit_price,
          NEW.discount_amount,NEW.discount_applied_by,NEW.tax_amount,NEW.line_total,NEW.specifications)
         IS NOT DISTINCT FROM
         (OLD.product_id,OLD.quantity,OLD.unit_id,OLD.bundle_count,OLD.estimated_weight,OLD.unit_price,
          OLD.discount_amount,OLD.discount_applied_by,OLD.tax_amount,OLD.line_total,OLD.specifications) THEN
        RETURN NEW;
      END IF;
    END IF;
    v_org := CASE WHEN TG_OP='DELETE' THEN OLD.organization_id ELSE NEW.organization_id END;
    v_req := CASE WHEN TG_OP='DELETE' THEN OLD.purchase_request_id ELSE NEW.purchase_request_id END;
    UPDATE request_commercial_state SET is_dirty=TRUE, updated_at=clock_timestamp()
     WHERE organization_id=v_org AND request_id=v_req;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_request_dirty AFTER UPDATE OF customer_id, requested_delivery_date, request_discount_amount, discount_applied_by
ON purchase_requests FOR EACH ROW EXECUTE FUNCTION trg_stage5_mark_request_dirty();
CREATE TRIGGER trg_stage5_item_dirty AFTER INSERT OR UPDATE OR DELETE ON purchase_request_items
FOR EACH ROW EXECUTE FUNCTION trg_stage5_mark_request_dirty();

-- Source item creation timestamp participates in deterministic line ordering and is immutable.
CREATE OR REPLACE FUNCTION trg_stage5_item_created_at_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'purchase request item created_at is immutable' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_item_created_at_guard BEFORE UPDATE ON purchase_request_items
FOR EACH ROW EXECUTE FUNCTION trg_stage5_item_created_at_guard();

-- Policies are immutable except one-way deactivation.
CREATE OR REPLACE FUNCTION trg_stage5_policy_guard() RETURNS trigger AS $$
DECLARE v_actor UUID; v_role TEXT; v_org UUID;
BEGIN
  v_actor := NULLIF(current_setting('app.user_id',true),'')::uuid;
  IF TG_OP='INSERT' THEN v_org := NEW.organization_id; ELSE v_org := OLD.organization_id; END IF;
  SELECT r.code INTO v_role FROM users u JOIN roles r ON r.id=u.role_id AND r.organization_id=u.organization_id
    WHERE u.id=v_actor AND u.organization_id=v_org AND u.is_active AND r.is_active;
  IF TG_OP='INSERT' THEN
    IF v_role IS DISTINCT FROM 'ADMIN' OR NEW.created_by IS DISTINCT FROM v_actor OR NOT EXISTS
       (SELECT 1 FROM roles WHERE organization_id=NEW.organization_id AND code=NEW.role_code AND is_active) THEN
      RAISE EXCEPTION 'discount policy creator or role is invalid' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'discount policies are immutable' USING ERRCODE='55000'; END IF;
  IF (NEW.organization_id,NEW.scope_type,NEW.scope_id,NEW.role_code,NEW.version,NEW.direct_limit_amount,
      NEW.direct_limit_percent,NEW.approver_limit_amount,NEW.approver_limit_percent,NEW.created_by,NEW.created_at)
     IS DISTINCT FROM
     (OLD.organization_id,OLD.scope_type,OLD.scope_id,OLD.role_code,OLD.version,OLD.direct_limit_amount,
      OLD.direct_limit_percent,OLD.approver_limit_amount,OLD.approver_limit_percent,OLD.created_by,OLD.created_at)
     OR (OLD.is_active=FALSE AND NEW IS DISTINCT FROM OLD)
  THEN RAISE EXCEPTION 'discount policy content is immutable' USING ERRCODE='55000'; END IF;
  IF OLD.is_active AND NOT NEW.is_active THEN
    IF v_role IS DISTINCT FROM 'ADMIN' THEN RAISE EXCEPTION 'ADMIN role required to deactivate a policy' USING ERRCODE='42501'; END IF;
    NEW.deactivated_at := clock_timestamp();
    NEW.deactivated_by := v_actor;
  ELSIF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
    RAISE EXCEPTION 'discount policy may only be deactivated' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_policy_guard BEFORE INSERT OR UPDATE OR DELETE ON discount_policies
FOR EACH ROW EXECUTE FUNCTION trg_stage5_policy_guard();

-- Snapshots and history are append-only.
CREATE OR REPLACE FUNCTION trg_stage5_immutable_row() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Stage 5 record is immutable' USING ERRCODE='55000'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_snapshot_immutable BEFORE UPDATE OR DELETE ON commercial_snapshots
FOR EACH ROW EXECUTE FUNCTION trg_stage5_immutable_row();
CREATE TRIGGER trg_stage5_snapshot_item_immutable BEFORE UPDATE OR DELETE ON commercial_snapshot_items
FOR EACH ROW EXECUTE FUNCTION trg_stage5_immutable_row();
CREATE TRIGGER trg_stage5_history_immutable BEFORE UPDATE OR DELETE ON discount_approval_history
FOR EACH ROW EXECUTE FUNCTION trg_stage5_immutable_row();
CREATE TRIGGER trg_stage5_discount_event_immutable BEFORE UPDATE OR DELETE ON discount_application_events
FOR EACH ROW EXECUTE FUNCTION trg_stage5_immutable_row();

-- Replace only the request state guard function; existing trigger from 002 remains attached.
CREATE OR REPLACE FUNCTION trg_purchase_requests_guard() RETURNS TRIGGER AS $$
DECLARE v_approval discount_approvals%ROWTYPE; v_state request_commercial_state%ROWTYPE;
        v_actor UUID; v_ok BOOLEAN := FALSE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.status <> 'DRAFT' THEN RAISE EXCEPTION 'purchase request cannot be deleted' USING ERRCODE='55000'; END IF;
    RETURN OLD;
  END IF;
  IF (NEW.organization_id,NEW.request_number,NEW.created_by,NEW.created_at)
     IS DISTINCT FROM (OLD.organization_id,OLD.request_number,OLD.created_by,OLD.created_at) THEN
    RAISE EXCEPTION 'immutable request identity fields' USING ERRCODE='55000';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT ((OLD.status='DRAFT' AND NEW.status IN ('SUBMITTED','CANCELLED')) OR
            (OLD.status='SUBMITTED' AND NEW.status='CANCELLED')) THEN
      IF OLD.status='SUBMITTED' AND NEW.status='DRAFT' THEN
        v_actor := NULLIF(current_setting('app.user_id',true),'')::uuid;
        SELECT * INTO v_state FROM request_commercial_state
         WHERE organization_id=OLD.organization_id AND request_id=OLD.id;
        SELECT a.* INTO v_approval FROM discount_approvals a
         WHERE a.organization_id=OLD.organization_id AND a.request_id=OLD.id
           AND a.status='REJECTED' AND a.rejected_txid=txid_current()
           AND a.decided_by=v_actor
           AND a.commercial_version=v_state.current_version
           AND EXISTS (SELECT 1 FROM discount_approval_history h WHERE h.organization_id=a.organization_id
             AND h.approval_id=a.id AND h.request_id=OLD.id AND h.event_type='REJECTED' AND h.actor_id=v_actor)
         ORDER BY a.decided_at DESC LIMIT 1;
        v_ok := FOUND AND v_actor IS NOT NULL AND NOT v_state.is_dirty;
        IF NOT v_ok THEN
          RAISE EXCEPTION 'SUBMITTED to DRAFT requires an atomic current rejected approval' USING ERRCODE='55000';
        END IF;
      ELSE
        RAISE EXCEPTION 'illegal request status transition % -> %',OLD.status,NEW.status USING ERRCODE='55000';
      END IF;
    END IF;
  END IF;
  IF OLD.status <> 'DRAFT' AND (NEW.customer_id,NEW.request_date,NEW.requested_delivery_date,
      NEW.subtotal,NEW.discount_total,NEW.tax_total,NEW.grand_total,NEW.request_discount_amount,NEW.discount_applied_by)
     IS DISTINCT FROM (OLD.customer_id,OLD.request_date,OLD.requested_delivery_date,
      OLD.subtotal,OLD.discount_total,OLD.tax_total,OLD.grand_total,OLD.request_discount_amount,OLD.discount_applied_by) THEN
    RAISE EXCEPTION 'commercial request fields are frozen outside DRAFT' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;


-- Cross-row ownership and maker-checker checks are enforced again in PostgreSQL.
CREATE OR REPLACE FUNCTION trg_stage5_approval_guard() RETURNS trigger AS $$
DECLARE v_actor UUID; v_role TEXT; v_request purchase_requests%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'approval records are retained' USING ERRCODE='55000'; END IF;
  v_actor := NULLIF(current_setting('app.user_id',true),'')::uuid;
  IF TG_OP='INSERT' THEN
    IF NEW.status<>'DRAFT' OR NEW.requested_by IS DISTINCT FROM v_actor THEN
      RAISE EXCEPTION 'approval must be requested by the authenticated actor' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM commercial_snapshots s JOIN purchase_requests r
                    ON r.id=s.request_id AND r.organization_id=s.organization_id
                    WHERE s.id=NEW.snapshot_id AND s.organization_id=NEW.organization_id
                      AND s.request_id=NEW.request_id AND s.commercial_version=NEW.commercial_version
                      AND s.approval_required AND r.status IN ('DRAFT','SUBMITTED')) THEN
      RAISE EXCEPTION 'approval snapshot/request mismatch' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
  END IF;
  IF (NEW.organization_id,NEW.request_id,NEW.snapshot_id,NEW.commercial_version,NEW.requested_by,NEW.requested_at)
     IS DISTINCT FROM (OLD.organization_id,OLD.request_id,OLD.snapshot_id,OLD.commercial_version,OLD.requested_by,OLD.requested_at) THEN
    RAISE EXCEPTION 'approval identity is immutable' USING ERRCODE='55000';
  END IF;
  IF NEW.status IN ('APPROVED','REJECTED') AND OLD.status='DRAFT' THEN
    SELECT r.* INTO v_request FROM purchase_requests r WHERE r.id=OLD.request_id AND r.organization_id=OLD.organization_id;
    SELECT ro.code INTO v_role FROM users u JOIN roles ro ON ro.id=u.role_id AND ro.organization_id=u.organization_id
      WHERE u.id=v_actor AND u.organization_id=OLD.organization_id AND u.is_active AND ro.is_active;
    IF v_role IS DISTINCT FROM 'ADMIN' THEN RAISE EXCEPTION 'ADMIN role required' USING ERRCODE='42501'; END IF;
    IF NEW.status='APPROVED' AND (v_actor IN (v_request.created_by,v_request.discount_applied_by,OLD.requested_by)
       OR EXISTS (SELECT 1 FROM purchase_request_items i WHERE i.organization_id=OLD.organization_id
                  AND i.purchase_request_id=OLD.request_id AND i.discount_applied_by=v_actor)
       OR EXISTS (SELECT 1 FROM commercial_snapshots ss WHERE ss.id=OLD.snapshot_id AND ss.organization_id=OLD.organization_id AND ss.created_by=v_actor)) THEN
      RAISE EXCEPTION 'self approval is forbidden' USING ERRCODE='42501';
    END IF;
    IF NEW.decided_by IS DISTINCT FROM v_actor THEN RAISE EXCEPTION 'decision actor mismatch' USING ERRCODE='42501'; END IF;
    IF NOT EXISTS (SELECT 1 FROM request_commercial_state cs JOIN commercial_snapshots ss
        ON ss.organization_id=cs.organization_id AND ss.request_id=cs.request_id AND ss.commercial_version=cs.current_version
        JOIN purchase_requests pr ON pr.id=cs.request_id AND pr.organization_id=cs.organization_id
        WHERE cs.organization_id=OLD.organization_id AND cs.request_id=OLD.request_id
          AND cs.current_version=OLD.commercial_version AND ss.id=OLD.snapshot_id AND NOT cs.is_dirty AND pr.status IN ('DRAFT','SUBMITTED')) THEN
      RAISE EXCEPTION 'approval snapshot is stale or request state is invalid' USING ERRCODE='55000';
    END IF;
  ELSIF NEW.status='INVALIDATED' AND OLD.status IN ('DRAFT','APPROVED') THEN
    IF v_actor IS NULL OR NEW.invalidated_by IS DISTINCT FROM v_actor OR NEW.invalidated_at IS NULL THEN RAISE EXCEPTION 'invalidator actor mismatch' USING ERRCODE='42501'; END IF;
  ELSE
    RAISE EXCEPTION 'illegal approval transition' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON discount_approvals
FOR EACH ROW EXECUTE FUNCTION trg_stage5_approval_guard();

CREATE OR REPLACE FUNCTION trg_stage5_snapshot_item_guard() RETURNS trigger AS $$
DECLARE v_request UUID;
BEGIN
  SELECT request_id INTO v_request FROM commercial_snapshots WHERE id=NEW.snapshot_id AND organization_id=NEW.organization_id;
  IF v_request IS NULL OR NOT EXISTS (SELECT 1 FROM purchase_request_items i WHERE i.id=NEW.source_item_id
      AND i.organization_id=NEW.organization_id AND i.purchase_request_id=v_request) THEN
    RAISE EXCEPTION 'snapshot item source does not belong to snapshot request' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_snapshot_item_guard BEFORE INSERT ON commercial_snapshot_items
FOR EACH ROW EXECUTE FUNCTION trg_stage5_snapshot_item_guard();

CREATE OR REPLACE FUNCTION trg_stage5_pi_link_guard() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM commercial_snapshots s WHERE s.id=NEW.snapshot_id AND s.organization_id=NEW.organization_id
      AND s.request_id=NEW.source_request_id AND s.commercial_version=NEW.commercial_version) THEN
    RAISE EXCEPTION 'pre-invoice link snapshot mismatch' USING ERRCODE='55000';
  END IF;
  IF NEW.approval_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM discount_approvals a WHERE a.id=NEW.approval_id
      AND a.organization_id=NEW.organization_id AND a.request_id=NEW.source_request_id AND a.snapshot_id=NEW.snapshot_id
      AND a.status='APPROVED') THEN
    RAISE EXCEPTION 'pre-invoice link approval mismatch' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stage5_pi_link_guard BEFORE INSERT OR UPDATE ON pre_invoice_commercial_links
FOR EACH ROW EXECUTE FUNCTION trg_stage5_pi_link_guard();
CREATE TRIGGER trg_stage5_pi_link_immutable BEFORE UPDATE OR DELETE ON pre_invoice_commercial_links
FOR EACH ROW EXECUTE FUNCTION trg_stage5_immutable_row();

-- Pre-Invoice must carry a snapshot link. Gate is invoked on DRAFT -> ISSUED only.
CREATE OR REPLACE FUNCTION trg_stage5_preinvoice_snapshot_gate() RETURNS trigger AS $$
DECLARE l pre_invoice_commercial_links%ROWTYPE; s commercial_snapshots%ROWTYPE;
        a discount_approvals%ROWTYPE; st request_commercial_state%ROWTYPE;
        v_count BIGINT; v_bad BIGINT;
BEGIN
  IF OLD.status='DRAFT' AND NEW.status='ISSUED' THEN
    SELECT * INTO l FROM pre_invoice_commercial_links
     WHERE organization_id=NEW.organization_id AND pre_invoice_id=NEW.id FOR SHARE;
      -- Stage 4 compatibility: if no link exists, let the original 004 trigger handle it
    IF NOT FOUND THEN
      RETURN NEW;
    END IF;
    SELECT * INTO s FROM commercial_snapshots WHERE id=l.snapshot_id AND organization_id=NEW.organization_id FOR SHARE;
    SELECT * INTO st FROM request_commercial_state WHERE organization_id=NEW.organization_id AND request_id=NEW.source_request_id FOR SHARE;
    IF s.id IS NULL OR st.request_id IS NULL OR st.is_dirty OR st.current_version<>l.commercial_version
       OR s.commercial_version<>l.commercial_version OR s.request_id<>NEW.source_request_id THEN
      RAISE EXCEPTION 'stale commercial snapshot' USING ERRCODE='55000';
    END IF;
    IF s.approval_required THEN
      SELECT * INTO a FROM discount_approvals WHERE id=l.approval_id AND organization_id=NEW.organization_id FOR SHARE;
      IF a.id IS NULL OR a.status<>'APPROVED' OR a.snapshot_id<>s.id OR a.commercial_version<>s.commercial_version THEN
        RAISE EXCEPTION 'required discount approval is missing or stale' USING ERRCODE='55000';
      END IF;
    END IF;
    IF (NEW.request_number,NEW.request_date,NEW.requested_delivery_date,NEW.request_notes,NEW.customer_id,
        NEW.customer_type,NEW.customer_name,NEW.customer_national_id,NEW.customer_economic_code,NEW.customer_registration_number,
        NEW.customer_mobile,NEW.customer_phone,NEW.customer_email,NEW.customer_address,NEW.customer_postal_code,
        NEW.seller_name,NEW.seller_legal_name,NEW.seller_national_id,NEW.seller_economic_code,NEW.seller_registration_number,
        NEW.seller_phone,NEW.seller_email,NEW.seller_address,NEW.seller_postal_code,
        NEW.subtotal,NEW.discount_total,NEW.tax_total,NEW.grand_total)
       IS DISTINCT FROM
       (s.request_number,s.request_date,s.requested_delivery_date,s.request_notes,s.customer_id,
        s.customer_snapshot->>'customer_type',s.customer_snapshot->>'name',s.customer_snapshot->>'national_id',
        s.customer_snapshot->>'economic_code',s.customer_snapshot->>'registration_number',s.customer_snapshot->>'mobile',
        s.customer_snapshot->>'phone',s.customer_snapshot->>'email',s.customer_snapshot->>'address',s.customer_snapshot->>'postal_code',
        s.seller_snapshot->>'name',s.seller_snapshot->>'legal_name',s.seller_snapshot->>'national_id',
        s.seller_snapshot->>'economic_code',s.seller_snapshot->>'registration_number',s.seller_snapshot->>'phone',
        s.seller_snapshot->>'email',s.seller_snapshot->>'address',s.seller_snapshot->>'postal_code',
        s.subtotal,s.discount_total,s.tax_total,s.grand_total) THEN
      RAISE EXCEPTION 'pre-invoice header differs from commercial snapshot' USING ERRCODE='55000';
    END IF;
    SELECT count(*) INTO v_count FROM commercial_snapshot_items WHERE organization_id=NEW.organization_id AND snapshot_id=s.id;
    IF v_count<>(SELECT count(*) FROM pre_invoice_items WHERE organization_id=NEW.organization_id AND pre_invoice_id=NEW.id) THEN
      RAISE EXCEPTION 'pre-invoice item count differs from commercial snapshot' USING ERRCODE='55000';
    END IF;
    SELECT count(*) INTO v_bad
      FROM commercial_snapshot_items ci
      LEFT JOIN pre_invoice_items pi ON pi.organization_id=ci.organization_id AND pi.pre_invoice_id=NEW.id AND pi.line_no=ci.line_no
     WHERE ci.organization_id=NEW.organization_id AND ci.snapshot_id=s.id
       AND (pi.id IS NULL OR pi.source_item_id<>ci.source_item_id OR pi.product_id<>ci.product_id
         OR pi.unit_id<>ci.unit_id OR pi.quantity<>ci.quantity OR pi.bundle_count<>ci.bundle_count
         OR pi.estimated_weight IS DISTINCT FROM ci.estimated_weight OR pi.unit_price<>ci.unit_price
         OR pi.discount_amount<>(ci.item_discount_amount+ci.request_discount_allocation)
         OR pi.tax_amount<>ci.tax_amount OR pi.line_total<>ci.line_total
         OR pi.product_code<>ci.product_code OR pi.product_name<>ci.product_name OR pi.unit_code<>ci.unit_code
         OR pi.unit_name<>ci.unit_name OR pi.notes IS DISTINCT FROM ci.notes OR pi.specifications<>ci.specifications);
    IF v_bad<>0 THEN RAISE EXCEPTION 'pre-invoice items differ from commercial snapshot' USING ERRCODE='55000'; END IF;
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_pre_invoices_snapshot_gate BEFORE UPDATE OF status ON pre_invoices
FOR EACH ROW EXECUTE FUNCTION trg_stage5_preinvoice_snapshot_gate();
