from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from tradeflow_core.inputs import data_envelope
from ..http_utils import API, client_ip, read_json, rid
from tradeflow_core import inputs


def build(ctx):
    r=APIRouter(prefix=API)
    @r.post("/purchase-requests/{req_id}/discount-approvals",status_code=201)
    async def request_approval(request:Request,req_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.approvals.request,p,req_id),rid(request))
    @r.get("/purchase-requests/{req_id}/discount-approvals")
    async def list_approvals(request:Request,req_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.approvals.list_for_request,p,req_id),rid(request))
    @r.get("/discount-approvals/{approval_id}/history")
    async def approval_history(request:Request,approval_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.approvals.history,p,approval_id),rid(request))
    @r.post("/discount-approvals/{approval_id}/approve")
    async def approve(request:Request,approval_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.approvals.approve,p,approval_id,client_ip(request)),rid(request))
    @r.post("/discount-approvals/{approval_id}/reject")
    async def reject(request:Request,approval_id:str):
        p=await ctx.principal(request); body=await read_json(request); inputs.strict_object(body,("reason",))
        return data_envelope(await run_in_threadpool(ctx.approvals.reject,p,approval_id,body.get("reason"),client_ip(request)),rid(request))
    return r
