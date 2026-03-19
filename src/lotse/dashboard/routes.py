"""Dashboard routes — serves HTMX-powered web UI."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from typing import Any

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from lotse import __version__

# Template setup — load from package directory
_template_dir = Path(__file__).parent / "templates"
_static_dir = Path(__file__).parent / "static"
_jinja = Environment(loader=FileSystemLoader(str(_template_dir)), autoescape=True)

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
    from lotse.inlets.api import _get_engine

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

    from lotse.inlets.api import _get_engine

    engine = _get_engine()
    results = engine.search(q.strip(), limit=20, mode="auto")
    return _render("partials/search_results.html", results=results, query=q)


@router.get("/partials/recent", response_class=HTMLResponse)
async def recent_partial() -> HTMLResponse:
    """Recent items table partial (loaded via HTMX)."""
    from lotse.inlets.api import _get_engine

    engine = _get_engine()
    items = engine.store.recent(limit=30)
    return _render("partials/recent.html", items=items)


@router.post("/partials/upload", response_class=HTMLResponse)
async def upload_partial(
    file: Annotated[UploadFile, File(description="File to classify and route")],
) -> HTMLResponse:
    """Handle file upload and return result partial."""
    from lotse.inlets.api import _get_engine

    engine = _get_engine()

    suffix = Path(file.filename or "upload").suffix
    stem = Path(file.filename or "upload").stem

    with tempfile.NamedTemporaryFile(
        prefix=f"{stem}_", suffix=suffix, delete=False
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

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

    # Get classification details from the most recent item
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
