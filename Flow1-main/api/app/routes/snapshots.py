from fastapi import APIRouter,Request
from starlette.concurrency import run_in_threadpool
from tradeflow_core.inputs import data_envelope
from tradeflow_core.authz import Cap,authorize
from ..http_utils import API,rid


def build(ctx):
    r=APIRouter(prefix=API)
    @r.get("/purchase-requests/{req_id}/commercial-snapshot")
    async def current_snapshot(request:Request,req_id:str):
        p=await ctx.principal(request)
        # The service performs tenant filtering; route remains intentionally read-only.
        return data_envelope(await run_in_threadpool(ctx.snapshots.current,p,req_id),rid(request))
    return r
