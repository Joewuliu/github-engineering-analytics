from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Every backend top-level path segment that must never be shadowed by the
# SPA fallback below -- an unmatched request under one of these still gets a
# real backend 404, not the React shell. Kept in sync by hand with the
# routers registered in app/main.py; deliberately a flat, explicit set, not
# a generic routing framework.
#
# "repositories" is deliberately NOT here: the bare `GET /repositories`
# route is already protected by ordinary registration precedence (it's a
# real route, so Starlette matches it before this fallback is ever reached)
# -- everything else under /repositories/* (e.g. /repositories/3) is the
# frontend's own React Router route, not a backend path at all, and must
# fall through to the SPA shell for deep links to work.
_RESERVED_PREFIXES = frozenset(
    {"auth", "me", "health", "ready", "docs", "redoc", "openapi.json", "assets"}
)


def configure_frontend(app: FastAPI, dist_path: Path) -> None:
    """Serve the built frontend (frontend/dist) if -- and only if -- it has
    actually been built.

    A backend-only checkout, or the `quality` CI job, never runs `npm run
    build`, and must keep working completely unchanged: no frontend/dist
    means no static routes are registered at all, never a startup failure.

    Must be called after every API router is already included on `app` --
    Starlette matches routes in registration order, so this running last is
    what lets /auth, /me, /health, /ready, /docs, /redoc, and /openapi.json
    all keep precedence automatically, with no special-casing here.
    """
    index_file = dist_path / "index.html"
    if not index_file.is_file():
        return

    assets_dir = dist_path / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # Reached only when nothing else matched. A path under a reserved
        # backend prefix is a genuinely wrong API call, not a frontend
        # route -- give it a real 404 instead of silently returning HTML.
        if full_path.split("/", 1)[0] in _RESERVED_PREFIXES:
            raise HTTPException(status_code=404)
        return FileResponse(index_file)
