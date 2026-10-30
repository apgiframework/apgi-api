"""
GET /updates.json — signed update feed for the APGI Appendix client.

Unauthenticated by design: the document carries no user data and is only trusted
after the client verifies its signature against a pinned key. See
``app/services/update_feed.py`` for the signing contract.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.update_feed import UpdateFeedError, build_feed

router = APIRouter(tags=["Updates"])


@router.get("/updates.json")
async def get_update_feed() -> JSONResponse:
    try:
        feed = build_feed(
            settings.update_manifest_path,
            settings.update_signing_private_key,
            settings.update_signing_key_id,
        )
    except UpdateFeedError as exc:
        # Never fall back to an unsigned document.
        raise HTTPException(status_code=503, detail=f"Update feed unavailable: {exc}") from exc
    return JSONResponse(feed, headers={"Cache-Control": "public, max-age=300"})
