"""The manager's area (view 3; US-208, US-304, US-402, US-601..US-607): portfolio matrix,
simulator assumptions, KPI, fairness, audit log and its CSV, outbox, product anomalies,
catalogue tasks and coupon rules.

Every endpoint of the group is a 501 stub until its route is added above add_stubs().
"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.routes.stubs import add_stubs

router = APIRouter()

add_stubs(router, "manager")
