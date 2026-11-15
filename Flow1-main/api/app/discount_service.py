"""Request-level discount entry and policy lookup."""
from decimal import Decimal
from psycopg.types.json import Jsonb
from tradeflow_core.discount import rial
from tradeflow_core.money import parse_money, line_total
from tradeflow_core.discount import percent_discount
from tradeflow_core.errors import AppError, ErrorCode
from tradeflow_core.authz import Cap, authorize
from tradeflow_core import inputs
from .commercial_mutations import invalidate_approvals

class DiscountService:
    def __init__(self,db): self.db=db
    def set_discount(self,p,request_id,body,ip=None):
        request_id=inputs.parse_uuid(request_id,"id")
        inputs.strict_object(body,("amount","percentage"))
        if ("amount" in body)==("percentage" in body):
            raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"PROVIDE_EXACTLY_ONE_OF_AMOUNT_OR_PERCENTAGE"})
        try:
            if "amount" in body:
                base_amount=parse_money(body["amount"],"amount"); percent_value=None; applied_as="AMOUNT"
            else:
                percent_value=body["percentage"]
                if not isinstance(percent_value,str): raise AppError(ErrorCode.INVALID_MONEY,details={"field":"percentage"})
                base_amount=None; applied_as="PERCENTAGE"
        except AppError: raise
        def work(c):
            c.execute("SELECT set_config('app.user_id',%s,true),set_config('app.organization_id',%s,true)",(str(p.user_id),str(p.org_id)))
            r=c.execute("SELECT * FROM purchase_requests WHERE id=%s AND organization_id=%s FOR UPDATE",(request_id,p.org_id)).fetchone()
            if not r: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_WRITE,caller_id=p.user_id,owner_id=str(r["created_by"]))
            if r["status"]!="DRAFT": raise AppError(ErrorCode.STATE_CONFLICT)
            c.execute("SELECT current_version FROM request_commercial_state WHERE organization_id=%s AND request_id=%s FOR UPDATE",(p.org_id,request_id)).fetchone()
            rows=c.execute("SELECT quantity,unit_price,discount_amount FROM purchase_request_items WHERE organization_id=%s AND purchase_request_id=%s ORDER BY created_at,id FOR UPDATE",
                           (p.org_id,request_id)).fetchall()
            gross=sum((rial(Decimal(x["quantity"])*Decimal(x["unit_price"])) for x in rows),Decimal(0))
            item_disc=sum((rial(x["discount_amount"]) for x in rows),Decimal(0))
            eligible=gross-item_disc
            amount=base_amount
            if amount is None:
                try: amount=percent_discount(eligible,percent_value)
                except Exception: raise AppError(ErrorCode.VALIDATION_ERROR,details={"field":"percentage"})
            if amount>eligible: raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"DISCOUNT_EXCEEDS_ELIGIBLE_AMOUNT"})
            if amount != rial(r["request_discount_amount"]) or str(p.user_id) != str(r["discount_applied_by"]):
                invalidate_approvals(c,p,request_id,"request-level discount changed")
            c.execute("UPDATE purchase_requests SET request_discount_amount=%s,discount_applied_by=%s,updated_by=%s,updated_at=clock_timestamp() WHERE id=%s AND organization_id=%s",
                      (amount,(p.user_id if amount>0 else None),p.user_id,request_id,p.org_id))
            c.execute("INSERT INTO discount_application_events(organization_id,request_id,actor_id,event_type,amount,event_data) VALUES(%s,%s,%s,%s,%s,%s)",
                      (p.org_id,request_id,p.user_id,"DISCOUNT_SET" if amount else "DISCOUNT_CLEARED",amount,Jsonb({"ip":ip,"applied_as":applied_as,"percentage":percent_value})))
            return {"request_id":str(request_id),"request_discount_amount":str(amount),"discount_applied_by":str(p.user_id) if amount>0 else None,"applied_as":applied_as}
        return self.db.run(work)
    def get_discount(self,p,request_id):
        request_id=inputs.parse_uuid(request_id,"id")
        def work(c):
            r=c.execute("SELECT request_discount_amount,discount_applied_by FROM purchase_requests WHERE id=%s AND organization_id=%s",(request_id,p.org_id)).fetchone()
            if not r: raise AppError(ErrorCode.NOT_FOUND)
            owner=c.execute("SELECT created_by FROM purchase_requests WHERE id=%s AND organization_id=%s",(request_id,p.org_id)).fetchone()["created_by"]
            authorize(p.role_code,Cap.REQUEST_READ,caller_id=p.user_id,owner_id=str(owner))
            return {"request_id":str(request_id),"request_discount_amount":str(r["request_discount_amount"]),"discount_applied_by":str(r["discount_applied_by"]) if r["discount_applied_by"] else None}
        return self.db.run(work)

    def create_policy(self,p,body):
        inputs.strict_object(body,("scope_type","scope_id","role_code","direct_limit_amount","direct_limit_percent","approver_limit_amount","approver_limit_percent"))
        if str(p.role_code).upper()!="ADMIN": raise AppError(ErrorCode.FORBIDDEN_ROLE)
        scope=body.get("scope_type","ORGANIZATION")
        if scope not in {"ORGANIZATION","PRODUCT"}: raise AppError(ErrorCode.VALIDATION_ERROR,details={"field":"scope_type"})
        scope_id=body.get("scope_id")
        if scope_id is not None: scope_id=inputs.parse_uuid(scope_id,"scope_id")
        if (scope=="ORGANIZATION" and scope_id is not None) or (scope=="PRODUCT" and not scope_id):
            raise AppError(ErrorCode.VALIDATION_ERROR,details={"field":"scope_id"})
        role=str(body.get("role_code","")).strip().upper()
        if not role: raise AppError(ErrorCode.VALIDATION_ERROR,details={"field":"role_code"})
        try:
            direct_amount=parse_money(body.get("direct_limit_amount","0"),"direct_limit_amount"); approver_amount=parse_money(body.get("approver_limit_amount","0"),"approver_limit_amount")
            if not isinstance(body.get("direct_limit_percent","0"),str) or not isinstance(body.get("approver_limit_percent","0"),str): raise ValueError()
            direct_pct=Decimal(body.get("direct_limit_percent","0")); approver_pct=Decimal(body.get("approver_limit_percent","0"))
            if not (Decimal(0)<=direct_pct<=Decimal(100) and Decimal(0)<=approver_pct<=Decimal(100)): raise ValueError()
        except AppError: raise
        except Exception: raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"INVALID_POLICY_LIMIT"})
        def work(c):
            c.execute("SELECT set_config('app.user_id',%s,true),set_config('app.organization_id',%s,true)",(str(p.user_id),str(p.org_id)))
            c.execute("SELECT id FROM organizations WHERE id=%s FOR UPDATE",(p.org_id,))
            role_exists=c.execute("SELECT 1 FROM roles WHERE organization_id=%s AND code=%s AND is_active",(p.org_id,role)).fetchone()
            if not role_exists: raise AppError(ErrorCode.VALIDATION_ERROR,details={"field":"role_code"})
            if scope_id:
                exists=c.execute("SELECT 1 FROM products WHERE id=%s AND organization_id=%s",(scope_id,p.org_id)).fetchone()
                if not exists: raise AppError(ErrorCode.REFERENCE_NOT_FOUND)
            version=c.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM discount_policies WHERE organization_id=%s AND scope_type=%s AND scope_id IS NOT DISTINCT FROM %s AND role_code=%s",
                              (p.org_id,scope,scope_id,role)).fetchone()["v"]
            row=c.execute("""INSERT INTO discount_policies(organization_id,scope_type,scope_id,role_code,version,direct_limit_amount,
              direct_limit_percent,approver_limit_amount,approver_limit_percent,created_by)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (p.org_id,scope,scope_id,role,version,direct_amount,direct_pct,approver_amount,approver_pct,p.user_id)).fetchone()
            return {k:(str(v) if isinstance(v,Decimal) else str(v) if k in {"id","organization_id","scope_id","created_by"} and v is not None else v) for k,v in dict(row).items()}
        return self.db.run(work)

    def list_policies(self,p):
        if str(p.role_code).upper()!="ADMIN": raise AppError(ErrorCode.FORBIDDEN_ROLE)
        def work(c):
            rows=c.execute("SELECT * FROM discount_policies WHERE organization_id=%s ORDER BY scope_type,scope_id,role_code,version DESC",(p.org_id,)).fetchall()
            return [{k:(str(v) if isinstance(v,Decimal) else str(v) if k in {"id","organization_id","scope_id","created_by","deactivated_by"} and v is not None else v) for k,v in dict(x).items()} for x in rows]
        return self.db.run(work)

    def deactivate_policy(self,p,policy_id):
        policy_id=inputs.parse_uuid(policy_id,"policy_id")
        if str(p.role_code).upper()!="ADMIN": raise AppError(ErrorCode.FORBIDDEN_ROLE)
        def work(c):
            c.execute("SELECT set_config('app.user_id',%s,true),set_config('app.organization_id',%s,true)",(str(p.user_id),str(p.org_id)))
            row=c.execute("SELECT * FROM discount_policies WHERE id=%s AND organization_id=%s FOR UPDATE",(policy_id,p.org_id)).fetchone()
            if not row: raise AppError(ErrorCode.NOT_FOUND)
            if not row["is_active"]: raise AppError(ErrorCode.STATE_CONFLICT)
            c.execute("UPDATE discount_policies SET is_active=FALSE WHERE id=%s AND organization_id=%s",(policy_id,p.org_id))
            return {"id":str(policy_id),"is_active":False}
        return self.db.run(work)

    def set_item_discount(self,p,item_id,body,ip=None):
        item_id=inputs.parse_uuid(item_id,"item_id")
        inputs.strict_object(body,("amount","percentage"),required=())
        if ("amount" in body)==("percentage" in body):
            raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"PROVIDE_EXACTLY_ONE_OF_AMOUNT_OR_PERCENTAGE"})
        try:
            if "amount" in body:
                base_amount=parse_money(body["amount"],"amount"); percent_value=None
            else:
                percent_value=body["percentage"]
                if not isinstance(percent_value,str): raise AppError(ErrorCode.INVALID_MONEY,details={"field":"percentage"})
                base_amount=None
        except AppError: raise
        def work(c):
            hint=c.execute("SELECT purchase_request_id FROM purchase_request_items WHERE id=%s AND organization_id=%s",(item_id,p.org_id)).fetchone()
            if not hint: raise AppError(ErrorCode.NOT_FOUND)
            request_id=hint["purchase_request_id"]
            r=c.execute("SELECT * FROM purchase_requests WHERE id=%s AND organization_id=%s FOR UPDATE",(request_id,p.org_id)).fetchone()
            if not r: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_WRITE,caller_id=p.user_id,owner_id=str(r["created_by"]))
            if r["status"]!="DRAFT": raise AppError(ErrorCode.STATE_CONFLICT)
            c.execute("SELECT current_version FROM request_commercial_state WHERE organization_id=%s AND request_id=%s FOR UPDATE",(p.org_id,request_id)).fetchone()
            row=c.execute("SELECT * FROM purchase_request_items WHERE id=%s AND organization_id=%s FOR UPDATE",(item_id,p.org_id)).fetchone()
            if not row: raise AppError(ErrorCode.NOT_FOUND)
            gross=rial(Decimal(row["quantity"])*Decimal(row["unit_price"]))
            amount_to_apply=base_amount
            if amount_to_apply is None:
                try: amount_to_apply=percent_discount(gross,percent_value)
                except Exception: raise AppError(ErrorCode.VALIDATION_ERROR,details={"field":"percentage"})
            if amount_to_apply>gross: raise AppError(ErrorCode.VALIDATION_ERROR,details={"reason":"DISCOUNT_EXCEEDS_ITEM_GROSS"})
            old_amount=rial(row["discount_amount"])
            old_actor=str(row["discount_applied_by"]) if row["discount_applied_by"] else None
            new_actor=str(p.user_id) if amount_to_apply>0 else None
            if amount_to_apply!=old_amount or old_actor!=new_actor:
                invalidate_approvals(c,p,request_id,"item discount changed")
                new_total=line_total(Decimal(row["quantity"]),Decimal(row["unit_price"]),amount_to_apply,Decimal(row["tax_amount"]))
                c.execute("UPDATE purchase_request_items SET discount_amount=%s,discount_applied_by=%s,line_total=%s WHERE id=%s AND organization_id=%s",
                          (amount_to_apply,new_actor,new_total,item_id,p.org_id))
                c.execute("UPDATE purchase_requests SET updated_by=%s,updated_at=clock_timestamp() WHERE id=%s AND organization_id=%s",
                          (p.user_id,request_id,p.org_id))
                c.execute("INSERT INTO discount_application_events(organization_id,request_id,actor_id,event_type,amount,event_data) VALUES(%s,%s,%s,%s,%s,%s)",
                          (p.org_id,request_id,p.user_id,"DISCOUNT_SET" if amount_to_apply else "DISCOUNT_CLEARED",amount_to_apply,Jsonb({"source_item_id":str(item_id),"ip":ip})))
            return {"item_id":str(item_id),"request_id":str(request_id),"discount_amount":str(amount_to_apply),"discount_applied_by":new_actor}
        return self.db.run(work)

    def get_item_discount(self,p,item_id):
        item_id=inputs.parse_uuid(item_id,"item_id")
        def work(c):
            row=c.execute("SELECT i.discount_amount,i.discount_applied_by,i.purchase_request_id,r.created_by FROM purchase_request_items i JOIN purchase_requests r ON r.id=i.purchase_request_id AND r.organization_id=i.organization_id WHERE i.id=%s AND i.organization_id=%s",
                          (item_id,p.org_id)).fetchone()
            if not row: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_READ,caller_id=p.user_id,owner_id=str(row["created_by"]))
            return {"item_id":str(item_id),"request_id":str(row["purchase_request_id"]),"discount_amount":str(row["discount_amount"]),
                    "discount_applied_by":str(row["discount_applied_by"]) if row["discount_applied_by"] else None}
        return self.db.run(work)
