"""Transactional Commercial Snapshot service. Caller identity is bound to the PostgreSQL transaction."""
from __future__ import annotations
from decimal import Decimal
from datetime import date, datetime
from uuid import UUID
from psycopg.types.json import Jsonb
from tradeflow_core.snapshot import build_snapshot
from tradeflow_core.discount import approval_required, select_policy, rial
from tradeflow_core.errors import AppError, ErrorCode
from tradeflow_core import inputs
from tradeflow_core.authz import Cap, authorize


class SnapshotService:
    def __init__(self, db): self.db = db

    @staticmethod
    def identity(conn, p):
        conn.execute("SELECT set_config('app.user_id',%s,true), set_config('app.organization_id',%s,true)",
                     (str(p.user_id), str(p.org_id)))

    def create_in_tx(self, conn, p, request_id):
        self.identity(conn, p)
        req = conn.execute("SELECT * FROM purchase_requests WHERE id=%s AND organization_id=%s FOR UPDATE",
                           (request_id,p.org_id)).fetchone()
        if req is None: raise AppError(ErrorCode.NOT_FOUND)
        state = conn.execute("SELECT * FROM request_commercial_state WHERE request_id=%s AND organization_id=%s FOR UPDATE",
                             (request_id,p.org_id)).fetchone()
        if state is None:
            conn.execute("INSERT INTO request_commercial_state(organization_id,request_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",(p.org_id,request_id))
            state = conn.execute("SELECT * FROM request_commercial_state WHERE request_id=%s AND organization_id=%s FOR UPDATE",(request_id,p.org_id)).fetchone()
        if req["status"] == "CANCELLED": raise AppError(ErrorCode.STATE_CONFLICT)
        if not state["is_dirty"]:
            current = conn.execute("SELECT * FROM commercial_snapshots WHERE organization_id=%s AND request_id=%s AND commercial_version=%s",
                                   (p.org_id,request_id,state["current_version"])).fetchone()
            if current: return current
        customer = conn.execute("SELECT * FROM customers WHERE id=%s AND organization_id=%s",(req["customer_id"],p.org_id)).fetchone()
        items = conn.execute("""SELECT i.*,p.code AS product_code,p.name AS product_name,u.code AS unit_code,u.name AS unit_name
          FROM purchase_request_items i JOIN products p ON p.id=i.product_id AND p.organization_id=i.organization_id
          JOIN units u ON u.id=i.unit_id AND u.organization_id=i.organization_id
          WHERE i.organization_id=%s AND i.purchase_request_id=%s ORDER BY i.created_at,i.id FOR UPDATE OF i""",(p.org_id,request_id)).fetchall()
        if not items: raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"EMPTY_REQUEST"})
        def role_of(actor_id):
            row=conn.execute("SELECT r.code FROM users u JOIN roles r ON r.id=u.role_id AND r.organization_id=u.organization_id WHERE u.id=%s AND u.organization_id=%s",
                             (actor_id,p.org_id)).fetchone()
            return row["code"] if row else ""

        def get_policy(role_code, scope_type, scope_id=None):
            if scope_type=="ORGANIZATION":
                row=conn.execute("""SELECT * FROM discount_policies WHERE organization_id=%s AND scope_type='ORGANIZATION'
                  AND scope_id IS NULL AND role_code=%s AND is_active ORDER BY version DESC LIMIT 1 FOR SHARE""",(p.org_id,role_code)).fetchone()
            else:
                row=conn.execute("""SELECT * FROM discount_policies WHERE organization_id=%s AND scope_type='PRODUCT'
                  AND scope_id=%s AND role_code=%s AND is_active ORDER BY version DESC LIMIT 1 FOR SHARE""",(p.org_id,scope_id,role_code)).fetchone()
            return row

        def policy_json(row, required=False):
            if row is None: return None
            out={}
            for k,v in dict(row).items():
                if k in {"created_at","deactivated_at"}: continue
                if isinstance(v,Decimal): out[k]=str(v)
                elif hasattr(v,"isoformat"): out[k]=v.isoformat()
                elif v is not None and k in {"id","organization_id","scope_id","created_by","deactivated_by"}: out[k]=str(v)
                else: out[k]=v
            out["approval_required"]=bool(required)
            return out

        approval_needed=False
        item_policy_records=[]
        for row in items:
            disc=rial(row["discount_amount"])
            applied_by=row["discount_applied_by"] or req["created_by"]
            chosen=None; required=False
            if disc:
                role_code=role_of(applied_by)
                chosen=get_policy(role_code,"PRODUCT",row["product_id"]) or get_policy(role_code,"ORGANIZATION")
                if chosen is None: raise AppError(ErrorCode.STATE_CONFLICT,details={"reason":"NO_ACTIVE_DISCOUNT_POLICY","source_item_id":str(row["id"])})
                gross=rial(Decimal(row["quantity"])*Decimal(row["unit_price"]))
                required=approval_required(disc,gross,chosen["direct_limit_amount"],chosen["direct_limit_percent"])
                if disc>rial(chosen["approver_limit_amount"]) or (gross and disc*Decimal(100)/gross>Decimal(str(chosen["approver_limit_percent"]))):
                    raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"DISCOUNT_EXCEEDS_APPROVER_LIMIT","source_item_id":str(row["id"])})
                approval_needed=approval_needed or required
            row["policy_snapshot"]=policy_json(chosen,required) or {}
            item_policy_records.append({"source_item_id":str(row["id"]),"policy":row["policy_snapshot"]})

        request_discount=rial(req["request_discount_amount"])
        request_policy=None; request_required=False
        if request_discount:
            actor=req["discount_applied_by"] or req["created_by"]
            role_code=role_of(actor)
            product_ids={str(x["product_id"]) for x in items}
            org_policy=get_policy(role_code,"ORGANIZATION")
            product_policies=[]
            if len(product_ids)==1:
                only=next(iter(product_ids))
                pp=get_policy(role_code,"PRODUCT",only)
                if pp: product_policies=[pp]
            request_policy=select_policy(product_policies,org_policy,product_ids)
            if request_policy is None: raise AppError(ErrorCode.STATE_CONFLICT,details={"reason":"NO_ACTIVE_DISCOUNT_POLICY","scope":"REQUEST"})
            eligible=sum((rial(Decimal(x["quantity"])*Decimal(x["unit_price"]))-rial(x["discount_amount"]) for x in items),Decimal(0))
            request_required=approval_required(request_discount,eligible,request_policy["direct_limit_amount"],request_policy["direct_limit_percent"])
            if request_discount>rial(request_policy["approver_limit_amount"]) or (eligible and request_discount*Decimal(100)/eligible>Decimal(str(request_policy["approver_limit_percent"]))):
                raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"DISCOUNT_EXCEEDS_APPROVER_LIMIT","scope":"REQUEST"})
            approval_needed=approval_needed or request_required
        request_policy_json=policy_json(request_policy,request_required)
        policy_data={"request_discount":request_policy_json,"item_discounts":item_policy_records,"approval_required":approval_needed}
        next_version = int(state["current_version"])+1
        try:
            snap = build_snapshot(request=req,customer=customer,items=items,policy=policy_data,request_discount=request_discount,
                                  created_by=p.user_id,commercial_version=next_version)
        except (ValueError, TypeError) as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"INVALID_COMMERCIAL_SNAPSHOT"}) from exc
        h=snap["header"]
        seller=conn.execute("SELECT * FROM organizations WHERE id=%s",(p.org_id,)).fetchone()
        seller_snapshot={k:seller.get(k) for k in ("name","legal_name","national_id","economic_code","registration_number","phone","email","address","postal_code")}
        sid=conn.execute("""INSERT INTO commercial_snapshots
          (organization_id,request_id,commercial_version,customer_id,customer_snapshot,request_number,request_date,requested_delivery_date,
           request_notes,seller_snapshot,subtotal,item_discount_total,request_discount_amount,request_discount_applied_by,discount_total,tax_total,grand_total,approval_required,policy_snapshot,created_by)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          (p.org_id,request_id,next_version,h["customer_id"],Jsonb(h["customer_snapshot"]),h["request_number"],h["request_date"],h["requested_delivery_date"],
           req["notes"],Jsonb(seller_snapshot),h["subtotal"],h["item_discount_total"],h["request_discount_amount"],h["request_discount_applied_by"],h["discount_total"],h["tax_total"],h["grand_total"],approval_needed,Jsonb(policy_data),p.user_id)).fetchone()["id"]
        for it in snap["items"]:
            cols=list(it.keys()); vals=[Jsonb(it[k]) if k in {"specifications","policy_snapshot"} else it[k] for k in cols]
            conn.execute(f"INSERT INTO commercial_snapshot_items(organization_id,snapshot_id,{','.join(cols)}) VALUES(%s,%s,{','.join(['%s']*len(cols))})",
                         [p.org_id,sid]+vals)
        conn.execute("UPDATE request_commercial_state SET current_version=%s,is_dirty=FALSE,updated_at=clock_timestamp() WHERE organization_id=%s AND request_id=%s",
                     (next_version,p.org_id,request_id))
        return conn.execute("SELECT * FROM commercial_snapshots WHERE id=%s AND organization_id=%s",(sid,p.org_id)).fetchone()

    def create(self,p,request_id):
        request_id=inputs.parse_uuid(request_id,"id")
        return self.db.run(lambda c:self.create_in_tx(c,p,request_id))

    def current(self,p,request_id):
        request_id=inputs.parse_uuid(request_id,"id")
        def work(c):
            owner=c.execute("SELECT created_by FROM purchase_requests WHERE organization_id=%s AND id=%s",(p.org_id,request_id)).fetchone()
            if not owner: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_READ,caller_id=p.user_id,owner_id=str(owner["created_by"]))
            st=c.execute("SELECT * FROM request_commercial_state WHERE organization_id=%s AND request_id=%s",(p.org_id,request_id)).fetchone()
            if not st or st["is_dirty"]: return None
            snap=c.execute("SELECT * FROM commercial_snapshots WHERE organization_id=%s AND request_id=%s AND commercial_version=%s",
                             (p.org_id,request_id,st["current_version"])).fetchone()
            if snap is None: return None
            def encode(row):
                result={}
                for key,value in dict(row).items():
                    if isinstance(value,Decimal): result[key]=str(value)
                    elif isinstance(value,UUID): result[key]=str(value)
                    elif isinstance(value,(date,datetime)): result[key]=value.isoformat()
                    else: result[key]=value
                return result
            out=encode(snap)
            out["items"]=[encode(x) for x in c.execute("SELECT * FROM commercial_snapshot_items WHERE organization_id=%s AND snapshot_id=%s ORDER BY line_no",(p.org_id,snap["id"])).fetchall()]
            return out
        return self.db.run(work)
