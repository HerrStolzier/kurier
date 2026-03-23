"""REST API inlet — FastAPI server for external capture and search."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from arkiv import __version__
from arkiv.core.config import ArkivConfig
from arkiv.core.engine import Engine

# Engine is initialized lazily via lifespan or create_app()
_engine: Engine | None = None


def create_app(config: ArkivConfig | None = None) -> FastAPI:
    """Create a FastAPI app with the given config."""
    global _engine

    cfg = config or ArkivConfig.load()
    cfg.ensure_dirs()
    _engine = Engine(cfg)

    api = FastAPI(
        title="Kurier",
        description="Universal capture → classify → route. Your AI-powered data pilot.",
        version=__version__,
    )

    api.include_router(_build_router())

    # Mount dashboard (HTMX web UI)
    from arkiv.dashboard.routes import router as dashboard_router

    api.include_router(dashboard_router)

    # Redirect root to dashboard
    @api.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/")

    return api


# --- Response models ---


class IngestResponse(BaseModel):
    success: bool
    route_name: str
    destination: str
    message: str


class SearchResult(BaseModel):
    id: int
    category: str
    summary: str | None
    route_name: str
    created_at: str
    rrf_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    count: int
    results: list[SearchResult]


class StatusResponse(BaseModel):
    version: str
    total_items: int
    categories: dict[str, int]
    routes: dict[str, int]
    vec_enabled: bool
    embeddings: int | None = None


class HealthResponse(BaseModel):
    status: str
    version: str


# --- Router ---


def _get_engine() -> Engine:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return _engine


def _build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check endpoint."""
        return HealthResponse(status="ok", version=__version__)

    @router.post("/ingest/file", response_model=IngestResponse)
    async def ingest_file(
        file: Annotated[UploadFile, File(description="File to classify and route")],
    ) -> IngestResponse:
        """Upload a file to be classified and routed."""
        engine = _get_engine()

        # Save upload to temp file, preserving original filename
        suffix = Path(file.filename or "upload").suffix
        stem = Path(file.filename or "upload").stem

        with tempfile.NamedTemporaryFile(prefix=f"{stem}_", suffix=suffix, delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            result = engine.ingest_file(tmp_path)
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=str(e)) from e

        # Clean up temp file only if routing moved it
        tmp_path.unlink(missing_ok=True)

        return IngestResponse(
            success=result.success,
            route_name=result.route_name,
            destination=result.destination,
            message=result.message,
        )

    @router.post("/ingest/text", response_model=IngestResponse)
    async def ingest_text(
        text: Annotated[str, Form(description="Text content to classify")],
        name: Annotated[str, Form(description="Optional name")] = "api_input",
    ) -> IngestResponse:
        """Submit text to be classified."""
        engine = _get_engine()

        if not text.strip():
            raise HTTPException(status_code=422, detail="Text cannot be empty")

        result = engine.ingest_text(text, name=name)

        return IngestResponse(
            success=result.success,
            route_name=result.route_name,
            destination=result.destination,
            message=result.message,
        )

    @router.get("/search", response_model=SearchResponse)
    async def search_items(
        q: Annotated[str, Query(description="Search query", min_length=1)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        mode: Annotated[str, Query(description="fts, vec, or auto")] = "auto",
    ) -> SearchResponse:
        """Search processed items. Supports keyword, semantic, and hybrid search."""
        engine = _get_engine()

        if mode not in ("fts", "vec", "auto"):
            raise HTTPException(status_code=422, detail="mode must be 'fts', 'vec', or 'auto'")

        results = engine.search(q, limit=limit, mode=mode)

        return SearchResponse(
            query=q,
            mode=mode,
            count=len(results),
            results=[
                SearchResult(
                    id=r["id"],
                    category=r["category"],
                    summary=r.get("summary"),
                    route_name=r["route_name"],
                    created_at=r["created_at"],
                    rrf_score=r.get("rrf_score"),
                )
                for r in results
            ],
        )

    @router.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Get processing statistics."""
        engine = _get_engine()
        s = engine.stats()

        return StatusResponse(
            version=__version__,
            total_items=s["total_items"],
            categories=s["categories"],
            routes=s["routes"],
            vec_enabled=s["vec_enabled"],
            embeddings=s.get("embeddings"),
        )

    @router.get("/recent")
    async def recent_items(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        """Get most recently processed items."""
        engine = _get_engine()
        result: list[dict[str, Any]] = engine.store.recent(limit=limit)
        return result

    return router
