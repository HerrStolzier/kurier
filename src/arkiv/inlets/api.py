"""REST API inlet — FastAPI server for external capture and search."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from arkiv import __version__
from arkiv.application import AppContext
from arkiv.application.ingest import ingest_file as ingest_file_workflow
from arkiv.application.ingest import ingest_text as ingest_text_workflow
from arkiv.application.search import search_items as search_items_workflow
from arkiv.application.status import get_recent_items
from arkiv.application.status import get_status as get_status_workflow
from arkiv.core.config import ArkivConfig
from arkiv.core.engine import Engine
from arkiv.core.upload import validate_and_save

logger = logging.getLogger(__name__)

# Shared application runtime is initialized by create_app()
_app_context: AppContext | None = None


def create_app(
    config: ArkivConfig | None = None,
    api_key: str | None = None,
    localhost_only: bool = False,
) -> FastAPI:
    """Create a FastAPI app with the given config.

    Args:
        config: Optional ArkivConfig to use. Loaded from disk if not provided.
        api_key: Optional API key required for non-localhost requests.
        localhost_only: When True and no api_key is set, block all non-localhost access.
            Defaults to False (no restriction). Set to True via ``kurier serve`` for
            local-only protection.
    """
    global _app_context

    from arkiv.core.auth import ApiKeyMiddleware, CsrfMiddleware, generate_csrf_token

    cfg = config or ArkivConfig.load()
    _app_context = AppContext(cfg)

    api = FastAPI(
        title="Kurier",
        description="Universal capture → classify → route. Your AI-powered data pilot.",
        version=__version__,
    )

    csrf_token = generate_csrf_token()
    api.add_middleware(CsrfMiddleware, token=csrf_token, api_key=api_key)

    # Add auth middleware only when network access control is actually needed.
    if api_key or localhost_only:
        api.add_middleware(ApiKeyMiddleware, api_key=api_key, localhost_only=localhost_only)

    api.include_router(_build_router())

    # Mount dashboard (HTMX web UI)
    from arkiv.dashboard.routes import _static_app, set_csrf_token
    from arkiv.dashboard.routes import router as dashboard_router

    set_csrf_token(csrf_token)
    api.include_router(dashboard_router)
    api.mount("/dashboard/static", _static_app, name="dashboard-static")

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
    display_title: str
    category: str
    summary: str | None
    route_name: str
    created_at: str
    destination_name: str | None = None
    match_reason: str | None = None
    rrf_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    memory: bool = False
    count: int
    assist: dict[str, Any] | None = None
    results: list[SearchResult]


class ItemListEntry(BaseModel):
    id: int
    display_title: str
    category: str
    summary: str | None
    route_name: str
    created_at: str
    destination_name: str | None = None


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


def _get_context() -> AppContext:
    if _app_context is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return _app_context


def _get_engine() -> Engine:
    return _get_context().engine


def _build_router() -> APIRouter:
    router = APIRouter()

    def _serialize_item(item: dict[str, Any]) -> ItemListEntry:
        return ItemListEntry(
            id=item["id"],
            display_title=item.get("display_title") or item.get("destination_name") or "Unbenannt",
            category=item["category"],
            summary=item.get("summary"),
            route_name=item["route_name"],
            created_at=item["created_at"],
            destination_name=item.get("destination_name"),
        )

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check endpoint."""
        return HealthResponse(status="ok", version=__version__)

    @router.post("/ingest/file", response_model=IngestResponse)
    async def ingest_file(
        file: Annotated[UploadFile, File(description="File to classify and route")],
    ) -> IngestResponse:
        """Upload a file to be classified and routed."""
        ctx = _get_context()

        # Validate and stream to temp file
        tmp_path = await validate_and_save(file)

        try:
            result = ingest_file_workflow(ctx, tmp_path)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            logger.exception("API file ingest failed")
            raise HTTPException(
                status_code=500,
                detail="Document could not be processed",
            ) from exc

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
        ctx = _get_context()

        if not text.strip():
            raise HTTPException(status_code=422, detail="Text cannot be empty")

        try:
            result = ingest_text_workflow(ctx, text, name=name)
        except Exception as exc:
            logger.exception("API text ingest failed")
            raise HTTPException(
                status_code=500,
                detail="Text could not be processed",
            ) from exc

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
        memory: Annotated[bool, Query(description="Enable LLM query assist")] = False,
    ) -> SearchResponse:
        """Search processed items. Supports keyword, semantic, and hybrid search."""
        ctx = _get_context()

        if mode not in ("fts", "vec", "auto"):
            raise HTTPException(status_code=422, detail="mode must be 'fts', 'vec', or 'auto'")

        results, assist = search_items_workflow(ctx, q, limit=limit, mode=mode, memory=memory)

        return SearchResponse(
            query=q,
            mode=mode,
            memory=memory,
            count=len(results),
            assist=(
                {
                    "rewrites": assist.rewrites,
                    "filters": assist.filters,
                    "notes": assist.notes,
                }
                if assist
                else None
            ),
            results=[
                SearchResult(
                    **_serialize_item(r).model_dump(),
                    match_reason=r.get("match_reason"),
                    rrf_score=r.get("rrf_score"),
                )
                for r in results
            ],
        )

    @router.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Get processing statistics."""
        ctx = _get_context()
        s = get_status_workflow(ctx)

        return StatusResponse(
            version=__version__,
            total_items=s["total_items"],
            categories=s["categories"],
            routes=s["routes"],
            vec_enabled=s["vec_enabled"],
            embeddings=s.get("embeddings"),
        )

    @router.get("/recent", response_model=list[ItemListEntry])
    async def recent_items(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[ItemListEntry]:
        """Get most recently processed items."""
        ctx = _get_context()
        result: list[dict[str, Any]] = get_recent_items(ctx, limit=limit)
        return [_serialize_item(item) for item in result]

    return router
