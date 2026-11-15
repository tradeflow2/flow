from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from tradeflow_core.inputs import data_envelope
from ..http_utils import API, client_ip, read_json, rid


def build(ctx):
    r=APIRouter(prefix=API)
    @r.put("/purchase-requests/{req_id}/discount")
    async def set_discount(request:Request,req_id:str):
        p=await ctx.principal(request); body=await read_json(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.set_discount,p,req_id,body,client_ip(request)),rid(request))
    @r.put("/purchase-request-items/{item_id}/discount")
    async def set_item_discount(request:Request,item_id:str):
        p=await ctx.principal(request); body=await read_json(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.set_item_discount,p,item_id,body,client_ip(request)),rid(request))
    @r.get("/purchase-request-items/{item_id}/discount")
    async def get_item_discount(request:Request,item_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.get_item_discount,p,item_id),rid(request))
    @r.get("/purchase-requests/{req_id}/discount")
    async def get_discount(request:Request,req_id:str):
        p=await ctx.principal(request)
        return data_envelope(await run_in_threadpool(ctx.discounts.get_discount,p,req_id),rid(request))
    return r
