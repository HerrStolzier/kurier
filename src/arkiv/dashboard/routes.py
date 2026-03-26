"""Dashboard routes — serves HTMX-powered web UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from arkiv import __version__
from arkiv.core.upload import validate_and_save


def _from_json(value: str | None) -> list[str]:
    """Jinja2 filter: parse a JSON-encoded list of strings (e.g. stored tags)."""
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# Template setup — load from package directory
_template_dir = Path(__file__).parent / "templates"
_static_dir = Path(__file__).parent / "static"
_jinja = Environment(loader=FileSystemLoader(str(_template_dir)), autoescape=True)
_jinja.filters["from_json"] = _from_json

router = APIRouter(prefix="/dashboard")

# HTMX served locally for privacy (no CDN request)
_static_app = StaticFiles(directory=str(_static_dir))


def _render(template_name: str, **context: Any) -> HTMLResponse:
    """Render a Jinja2 template and return as HTML response."""
    template = _jinja.get_template(template_name)
    html = template.render(**context)
    return HTMLResponse(html)


router.mount("/static", _static_app, name="dashboard-static")


@router.get("/", response_class=HTMLResponse)
async def dashboard_index() -> HTMLResponse:
    """Main dashboard page."""
    return _render("dashboard.html", version=__version__)


@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial() -> HTMLResponse:
    """Stats cards partial (loaded via HTMX)."""
    from arkiv.inlets.api import _get_engine

    engine = _get_engine()
    stats = engine.stats()
    return _render("partials/stats.html", stats=stats)


@router.get("/partials/search", response_class=HTMLResponse)
async def search_partial(
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Search results partial (loaded via HTMX on keyup)."""
    if not q.strip():
        return HTMLResponse("")

    from arkiv.inlets.api import _get_engine

    engine = _get_engine()
    results = engine.search(q.strip(), limit=20, mode="auto")
    return _render("partials/search_results.html", results=results, query=q)


@router.get("/partials/recent", response_class=HTMLResponse)
async def recent_partial() -> HTMLResponse:
    """Recent items table partial (loaded via HTMX)."""
    from arkiv.inlets.api import _get_engine

    engine = _get_engine()
    items = engine.store.recent(limit=30)
    return _render("partials/recent.html", items=items)


@router.post("/partials/upload", response_class=HTMLResponse)
async def upload_partial(
    file: Annotated[UploadFile, File(description="File to classify and route")],
) -> HTMLResponse:
    """Handle file upload and return result partial."""
    from arkiv.inlets.api import _get_engine

    engine = _get_engine()

    # Validate and stream to temp file (raises HTTPException on invalid input)
    try:
        tmp_path = await validate_and_save(file)
    except Exception as e:
        return _render(
            "partials/upload_result.html",
            success=False,
            message=str(e),
            category="",
            confidence=0,
        )

    try:
        result = engine.ingest_file(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return _render(
            "partials/upload_result.html",
            success=False,
            message=str(e),
            category="",
            confidence=0,
        )

    tmp_path.unlink(missing_ok=True)

    # Fetch the most recently inserted item (by created_at) to get category/confidence.
    # Using the store's own recent() is safe here since ingest_file() already committed.
    recent = engine.store.recent(limit=1)
    category = recent[0]["category"] if recent else "unknown"
    confidence = recent[0]["confidence"] if recent else 0

    return _render(
        "partials/upload_result.html",
        success=result.success,
        message=result.message,
        category=category,
        confidence=confidence,
    )


@router.get("/partials/review", response_class=HTMLResponse)
async def review_partial() -> HTMLResponse:
    """Review queue: low-confidence items that may need manual correction."""
    from arkiv.inlets.api import _get_engine

    engine = _get_engine()
    items = engine.store.low_confidence(threshold=0.6, limit=50)
    return _render("partials/review.html", items=items)


@router.post("/partials/review/{item_id}/correct", response_class=HTMLResponse)
async def review_correct(
    item_id: int,
    category: Annotated[str, Form(description="New category")],
) -> HTMLResponse:
    """Correct the category of a low-confidence item. Returns empty HTML (item removed)."""
    from arkiv.inlets.api import _get_engine

    engine = _get_engine()
    try:
        engine.store.update_category(item_id, category.strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Return empty string — HTMX will swap the item out of the queue
    return HTMLResponse("")


@router.post("/partials/review/{item_id}/confirm", response_class=HTMLResponse)
async def review_confirm(item_id: int) -> HTMLResponse:
    """Confirm the classification of a low-confidence item. Returns empty HTML (item removed)."""
    from arkiv.inlets.api import _get_engine

    engine = _get_engine()
    try:
        engine.store.confirm_classification(item_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Return empty string — HTMX will swap the item out of the queue
    return HTMLResponse("")
