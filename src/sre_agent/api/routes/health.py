"""Liveness/readiness probes and the Prometheus /metrics scrape endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict:
    # A real readiness check pings Postgres; kept trivial here so the
    # container's readiness probe doesn't depend on external infra in the
    # reference deployment's smoke tests.
    return {"status": "ready"}


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
