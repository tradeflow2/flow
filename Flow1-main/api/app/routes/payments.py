"""Payments routes (Stage 6, S6-A)."""
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from tradeflow_core.inputs import data_envelope, list_envelope

from ..http_utils import API, client_ip, page, read_json, rid


def build(ctx) -> APIRouter:
    r = APIRouter(prefix=API)
    payments = ctx.payments

    @r.post("/pre-invoices/{pre_invoice_id}/payments", status_code=201)
    async def payments_create(request: Request, pre_invoice_id: str):
        p = await ctx.principal(request)
        body = await read_json(request)
        return data_envelope(
            await run_in_threadpool(payments.create, p, pre_invoice_id, body, client_ip(request)),
            rid(request),
        )

    @r.get("/pre-invoices/{pre_invoice_id}/payments")
    async def payments_list(request: Request, pre_invoice_id: str):
        p = await ctx.principal(request)
        return page(
            request,
            await run_in_threadpool(payments.list, p, pre_invoice_id, request.query_params),
        )

    @r.get("/payments/{payment_id}")
    async def payments_get(request: Request, payment_id: str):
        p = await ctx.principal(request)
        return data_envelope(
            await run_in_threadpool(payments.get, p, payment_id),
            rid(request),
        )

    @r.patch("/payments/{payment_id}")
    async def payments_patch(request: Request, payment_id: str):
        p = await ctx.principal(request)
        body = await read_json(request)
        return data_envelope(
            await run_in_threadpool(payments.patch, p, payment_id, body, client_ip(request)),
            rid(request),
        )

    return r