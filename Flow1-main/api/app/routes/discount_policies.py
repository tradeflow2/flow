from fastapi import APIRouter,Request
from starlette.concurrency import run_in_threadpool
from tradeflow_core.inputs import data_envelope
from ..http_utils import API,read_json,rid


def build(ctx):
    r=APIRouter(prefix=API)
    @r.get("/discount-policies")
    async def list_policies(request:Request):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.list_policies,p),rid(request))
    @r.post("/discount-policies",status_code=201)
    async def create_policy(request:Request):
        p=await ctx.principal(request); body=await read_json(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.create_policy,p,body),rid(request))
    @r.post("/discount-policies/{policy_id}/deactivate")
    async def deactivate_policy(request:Request,policy_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.deactivate_policy,p,policy_id),rid(request))
    return r
