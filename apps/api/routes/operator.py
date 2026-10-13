"""The operator's area (view 1; US-301..US-310, US-604): returns, decisions, regeneration,
evidence and link disputes.

Implemented here: GET /api/returns/{return_id}, the case card, as the reference implementation
for the route builders (services.cases.case_card). The other endpoints of the group are 501
stubs until they are added above the add_stubs() call.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from apps.api import schemas as s
from apps.api.errors import not_found
from apps.api.routes.stubs import add_stubs, responses
from apps.api.runtime import runtime
from apps.api.services.cases import case_card

router = APIRouter()
CASE_CARD = next(ep for ep in s.ENDPOINTS if ep.path == "/api/returns/{return_id}")


@router.get(
    CASE_CARD.path,
    response_model=s.CaseCard,
    responses=responses(CASE_CARD),
    summary=CASE_CARD.summary,
    tags=["operator"],
)
async def get_case_card(
    request: Request, return_id: Annotated[str, PathParam(max_length=96)]
) -> JSONResponse:
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        card = await case_card(conn, serving, return_id, rt.now())
    if card is None:
        raise not_found(f"return {return_id}")
    return JSONResponse(card.model_dump(mode="json"))


add_stubs(router, "operator")
