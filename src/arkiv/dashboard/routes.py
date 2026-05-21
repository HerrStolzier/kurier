"""Dashboard routes — serves HTMX-powered web UI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from arkiv import __version__
from arkiv.application.beta import record_beta_event
from arkiv.application.ingest import ingest_file as ingest_file_workflow
from arkiv.application.review import confirm_review_item, correct_review_item, get_review_items
from arkiv.application.search import search_items as search_items_workflow
from arkiv.application.status import get_recent_items, get_status
from arkiv.core.upload import validate_and_save

logger = logging.getLogger(__name__)


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
_csrf_token = ""


def set_csrf_token(token: str) -> None:
    """Set the per-process CSRF token used by dashboard templates."""
    global _csrf_token
    _csrf_token = token


def _render(template_name: str, **context: Any) -> HTMLResponse:
    """Render a Jinja2 template and return as HTML response."""
    template = _jinja.get_template(template_name)
    context.setdefault("csrf_token", _csrf_token)
    html = template.render(**context)
    return HTMLResponse(html)


@router.get("/", response_class=HTMLResponse)
async def dashboard_index() -> HTMLResponse:
    """Main dashboard page."""
    return _render("dashboard.html", version=__version__)


@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial() -> HTMLResponse:
    """Stats cards partial (loaded via HTMX)."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    stats = get_status(ctx)
    return _render("partials/stats.html", stats=stats)


@router.get("/partials/search", response_class=HTMLResponse)
async def search_partial(
    q: Annotated[str, Query()] = "",
    memory: Annotated[bool, Query()] = True,
) -> HTMLResponse:
    """Search results partial (loaded via HTMX on keyup)."""
    if not q.strip():
        return HTMLResponse("")

    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    results, assist = search_items_workflow(ctx, q.strip(), limit=20, mode="auto", memory=memory)
    if not results:
        record_beta_event(
            ctx,
            "search_no_results",
            "Suche ohne Treffer",
            severity="warn",
            context={"query": q.strip(), "memory": memory},
        )
    return _render(
        "partials/search_results.html",
        results=results,
        query=q,
        memory=memory,
        assist=assist,
    )


@router.get("/partials/recent", response_class=HTMLResponse)
async def recent_partial() -> HTMLResponse:
    """Recent items table partial (loaded via HTMX)."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    items = get_recent_items(ctx, limit=30)
    for item in items:
        original_path = item.get("original_path") or ""
        item["source_name"] = Path(str(original_path).replace("text://", "")).name or original_path
    return _render("partials/recent.html", items=items)


@router.post("/partials/upload", response_class=HTMLResponse)
async def upload_partial(
    file: Annotated[UploadFile, File(description="File to classify and route")],
) -> HTMLResponse:
    """Handle file upload and return result partial."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()

    # Validate and stream to temp file (raises HTTPException on invalid input)
    try:
        tmp_path = await validate_and_save(file)
    except Exception as exc:
        logger.exception("Dashboard upload preparation failed")
        record_beta_event(
            ctx,
            "upload_failed",
            "Upload konnte nicht vorbereitet werden",
            severity="error",
            context={"filename": file.filename or "unbekannt", "error": str(exc)},
        )
        return _render(
            "partials/upload_result.html",
            success=False,
            message="Upload konnte nicht vorbereitet werden.",
            category="",
            confidence=0,
        )

    try:
        result = ingest_file_workflow(ctx, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        logger.exception("Dashboard document processing failed")
        record_beta_event(
            ctx,
            "upload_failed",
            "Dokument konnte nicht verarbeitet werden",
            severity="error",
            context={"filename": file.filename or "unbekannt", "error": str(exc)},
        )
        return _render(
            "partials/upload_result.html",
            success=False,
            message="Dokument konnte nicht verarbeitet werden.",
            category="",
            confidence=0,
        )

    tmp_path.unlink(missing_ok=True)

    # Fetch the most recently inserted item (by created_at) to get category/confidence.
    recent = get_recent_items(ctx, limit=1)
    latest = recent[0] if recent else {}
    category = latest.get("category", "unknown")
    confidence = latest.get("confidence", 0)
    item_id = latest.get("id")
    route_name = latest.get("route_name", "")

    if confidence < 0.6 or route_name == "__review__":
        record_beta_event(
            ctx,
            "low_confidence_review",
            "Dokument braucht wahrscheinlich einen Blick",
            severity="warn",
            context={"category": category, "confidence": confidence, "route_name": route_name},
            item_id=item_id,
        )

    return _render(
        "partials/upload_result.html",
        success=result.success,
        message=result.message,
        category=category,
        confidence=confidence,
        source_name=file.filename or tmp_path.name,
        destination=result.destination,
        destination_name=latest.get("destination_name") or Path(result.destination).name,
        display_title=latest.get("display_title") or latest.get("destination_name") or category,
        route_name=route_name,
    )


@router.get("/partials/review", response_class=HTMLResponse)
async def review_partial() -> HTMLResponse:
    """Review queue: low-confidence items that may need manual correction."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    items = get_review_items(ctx, threshold=0.6, limit=50)
    return _render("partials/review.html", items=items)


@router.post("/partials/review/{item_id}/correct", response_class=HTMLResponse)
async def review_correct(
    item_id: int,
    category: Annotated[str, Form(description="New category")],
) -> HTMLResponse:
    """Correct the category of a low-confidence item. Returns empty HTML (item removed)."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    try:
        correct_review_item(ctx, item_id, category)
        record_beta_event(
            ctx,
            "category_corrected",
            "Kategorie manuell korrigiert",
            severity="info",
            context={"category": category.strip()},
            item_id=item_id,
        )
    except Exception as exc:
        logger.exception("Dashboard review correction failed")
        raise HTTPException(status_code=500, detail="Review action failed") from exc
    return _render(
        "partials/review_done.html",
        message=f"Erledigt: Kategorie wurde auf '{category.strip()}' gesetzt.",
    )


@router.post("/partials/review/{item_id}/confirm", response_class=HTMLResponse)
async def review_confirm(item_id: int) -> HTMLResponse:
    """Confirm the classification of a low-confidence item. Returns empty HTML (item removed)."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    try:
        confirm_review_item(ctx, item_id)
        record_beta_event(
            ctx,
            "classification_confirmed",
            "Unsichere Einordnung bestätigt",
            severity="info",
            item_id=item_id,
        )
    except Exception as exc:
        logger.exception("Dashboard review confirmation failed")
        raise HTTPException(status_code=500, detail="Review action failed") from exc
    return _render("partials/review_done.html", message="Erledigt: Einordnung bestätigt.")


@router.post("/partials/beta/problem", response_class=HTMLResponse)
async def beta_problem(
    message: Annotated[str, Form(description="Short problem description")],
    page: Annotated[str, Form()] = "dashboard",
) -> HTMLResponse:
    """Record a manually reported beta problem."""
    from arkiv.inlets.api import _get_context

    ctx = _get_context()
    text = message.strip()
    if not text:
        return _render(
            "partials/beta_feedback_result.html",
            success=False,
            message="Schreib kurz dazu, was gerade nicht gepasst hat.",
        )

    record_beta_event(
        ctx,
        "manual_feedback",
        text,
        severity="info",
        context={"page": page},
    )
    return _render(
        "partials/beta_feedback_result.html",
        success=True,
        message="Danke, ist lokal notiert. Das hilft uns beim Feinschliff.",
    )
