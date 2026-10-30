"""
Static host for the /console operator dashboard.

Only the shell is public (no data); every data call goes to /v1/ops/* with a
Bearer token. Files are served from web/console and listed explicitly so the
authentication middleware's exact-match public paths stay exact.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

CONSOLE_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "console"
ASSETS = {
    "console.js": "application/javascript",
    "console.css": "text/css",
}

router = APIRouter(tags=["Console"], include_in_schema=False)


@router.get("/console")
async def console_index() -> FileResponse:
    return FileResponse(CONSOLE_DIR / "index.html", media_type="text/html")


@router.get("/console/")
async def console_slash() -> RedirectResponse:
    return RedirectResponse("/console", status_code=307)


@router.get("/console/console.js")
async def console_js() -> FileResponse:
    return FileResponse(CONSOLE_DIR / "console.js", media_type=ASSETS["console.js"])


@router.get("/console/console.css")
async def console_css() -> FileResponse:
    return FileResponse(CONSOLE_DIR / "console.css", media_type=ASSETS["console.css"])
