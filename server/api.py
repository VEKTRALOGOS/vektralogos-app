"""Тонкий HTTP-шар над готовим Ф0–Ф1 трактом (Editor MVP, спека feat/editor-mvp).

НЕ новий pipeline — лише обгортка над існуючими функціями, щоб браузерний
редактор (client/) міг пройти: prompt → превью → print-ready PDF. Той самий
CanvasJSON і той самий .ttf, що йде на сервер, використовує і клієнт (інваріант
CLAUDE.md §4 п.1). Друкарський файл лишається вектором/CMYK; растрове превью —
тільки для екрану (§4 п.2).

Ендпоінти (спека §3):
  * POST /api/brief          {prompt, size?}  → {brief}
  * POST /api/canvas         {brief, size?}   → {id, canvas}   (canvas кешується під id)
  * GET  /api/preview/<id>                     → PNG (растр того ж вектора)
  * POST /api/render         {canvas}          → print-ready PDF | 422 needs_human
  * GET  /api/font/<file>                       → .ttf зі server/fonts (для Fabric FontFace)
  * GET  /                                      → client/index.html
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .brief import DesignBrief, prompt_to_brief
from .onboarding import export_stages, presets_payload
from .preflight_agent import preflight_agent
from .prompt_to_canvas import DEFAULT_PAPER, brief_from_prompt_to_canvas, resolve_size
from .render import FONTS_DIR, render_preview_png
from .schema import CanvasJSON

load_dotenv()

app = FastAPI(title="Vektralogos Editor MVP", version="1.0")

_CLIENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "client")

# Легкий in-memory кеш CanvasJSON під id — рівно щоб GET /api/preview/<id>
# міг растеризувати той самий вектор без повторної відправки з клієнта. MVP:
# без БД, без TTL; процес живе один сеанс демо/скріншоту.
_CANVAS_CACHE: dict[str, CanvasJSON] = {}

# Кеш готових print-ready PDF від онбординг-експорту (id -> bytes), щоб екран
# Result міг віддати файл на завантаження без повторного рендеру.
_EXPORT_CACHE: dict[str, bytes] = {}


class BriefRequest(BaseModel):
    prompt: str = Field(description="Вільний текст клієнта")
    size: str = Field(default=DEFAULT_PAPER, description="a4/a5/a6/card")


class CanvasRequest(BaseModel):
    brief: DesignBrief
    size: str = Field(default=DEFAULT_PAPER, description="a4/a5/a6/card")


@app.post("/api/brief")
def api_brief(req: BriefRequest) -> dict[str, Any]:
    """prompt → DesignBrief (обгортка над prompt_to_brief, claude-opus-5)."""
    try:
        brief = prompt_to_brief(req.prompt)
    except RuntimeError as exc:  # нема ключа / відмова моделі → 502
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"brief": brief.model_dump()}


@app.post("/api/canvas")
def api_canvas(req: CanvasRequest) -> dict[str, Any]:
    """DesignBrief → CanvasJSON (детермінований шаблонизатор). Кешує під id."""
    try:
        resolve_size(req.size)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    canvas = brief_from_prompt_to_canvas(req.brief, size=req.size)
    canvas_id = uuid.uuid4().hex
    _CANVAS_CACHE[canvas_id] = canvas
    return {"id": canvas_id, "canvas": canvas.model_dump()}


@app.get("/api/preview/{canvas_id}")
def api_preview(canvas_id: str) -> Response:
    """Растровий PNG-прев'ю того ж вектора (тільки екран, §4 п.2)."""
    canvas = _CANVAS_CACHE.get(canvas_id)
    if canvas is None:
        raise HTTPException(status_code=404, detail=f"Невідомий canvas id: {canvas_id}")
    png = render_preview_png(canvas)
    return Response(content=png, media_type="image/png")


@app.post("/api/render")
def api_render(canvas: dict[str, Any] = Body(..., embed=False)) -> Response:
    """CanvasJSON → print-ready PDF (render + preflight_agent).

    Агент авто-фіксить придатні до друку issue (bleed/розмір/DPI) і робить
    фінальний рендер один раз. status="ok" → повертаємо PDF; будь-що інше →
    422 з needs_human-звітом (лишились помилки, які редагування JSON не чинить).
    """
    try:
        spec = CanvasJSON.model_validate(canvas)
    except Exception as exc:  # невалідний CanvasJSON від клієнта → 400
        raise HTTPException(status_code=400, detail=f"Невалідний CanvasJSON: {exc}") from exc

    result = preflight_agent(spec)
    if result.status != "ok":
        report = {
            "status": "needs_human",
            "agent_status": result.status,
            "iterations": result.iterations,
            "issues": [
                {"level": i.level, "code": i.code, "message": i.message}
                for i in result.report.issues
            ],
        }
        return JSONResponse(status_code=422, content=report)

    return Response(
        content=result.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="vektralogos-print.pdf"'},
    )


@app.get("/api/font/{file_name}")
def api_font(file_name: str) -> FileResponse:
    """Віддає .ttf зі server/fonts — той самий файл, що й у рендері (інваріант)."""
    # Захист від traversal: лише базове ім'я з дозволеним розширенням.
    if os.path.basename(file_name) != file_name or not file_name.lower().endswith(
        (".ttf", ".otf")
    ):
        raise HTTPException(status_code=400, detail="Некоректне ім'я шрифту")
    path = os.path.join(FONTS_DIR, file_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Шрифт не знайдено: {file_name}")
    return FileResponse(path, media_type="font/ttf")


# --- Онбординг-флоу (спека onboarding-flow.md, TASKS #22) --------------------


@app.get("/api/presets")
def api_presets() -> dict[str, Any]:
    """Пресети кроку 3 (детерміновані, кирилиця в кожному). Кешує canvas під id."""
    presets = presets_payload()
    # Реєструємо кожен canvas у кеші, щоб /api/preview/<id> теж працював.
    for p in presets:
        cid = uuid.uuid4().hex
        _CANVAS_CACHE[cid] = CanvasJSON.model_validate(p["canvas"])
        p["preview_id"] = cid
    return {"presets": presets}


@app.post("/api/export/stream")
def api_export_stream(canvas: dict[str, Any] = Body(..., embed=False)) -> StreamingResponse:
    """SSE-стрім реальних стадій експорту (крок 5): converting_cmyk → checking_dpi
    → done. Мітки відповідають реальним фазам тракту, без фейкового таймера."""
    try:
        spec = CanvasJSON.model_validate(canvas)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Невалідний CanvasJSON: {exc}") from exc

    def _events():
        for stage, payload in export_stages(spec):
            if stage == "done":
                pdf = payload.pop("pdf")
                export_id = uuid.uuid4().hex
                _EXPORT_CACHE[export_id] = pdf
                payload["export_id"] = export_id
                payload["download_url"] = f"/api/download/{export_id}"
            yield f"event: {stage}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/download/{export_id}")
def api_download(export_id: str) -> Response:
    """Віддає print-ready PDF, збережений під час експорту (крок 6)."""
    pdf = _EXPORT_CACHE.get(export_id)
    if pdf is None:
        raise HTTPException(status_code=404, detail=f"Невідомий export id: {export_id}")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="vektralogos-print.pdf"'},
    )


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding() -> HTMLResponse:
    """7-кроковий онбординг-флоу (client/onboarding.html)."""
    path = os.path.join(_CLIENT_DIR, "onboarding.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="client/onboarding.html не знайдено")
    with open(path, "r", encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Єдина сторінка редактора (client/index.html)."""
    path = os.path.join(_CLIENT_DIR, "index.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="client/index.html не знайдено")
    with open(path, "r", encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


# Vendored Fabric.js (client/vendor) — сторінка самодостатня, без CDN.
_VENDOR_DIR = os.path.join(_CLIENT_DIR, "vendor")
if os.path.isdir(_VENDOR_DIR):
    app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")
