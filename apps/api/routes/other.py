"""The other areas (view 4 and the shop; US-401..US-403, US-501..US-503, US-701, US-702, US-902,
US-905): simulated checkout, warehouse inspections, the read-only agent, benchmarks and
telemetry.

Every endpoint of the group is a 501 stub until its route is added above add_stubs().
"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.routes.stubs import add_stubs

router = APIRouter()

add_stubs(router, "other")
