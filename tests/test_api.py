"""Офлайн-тести HTTP-шару Editor MVP (feat/editor-mvp), без виклику LLM.

Перевіряємо, що тонка обгортка (server/api.py) не заводить окремий рендер-шлях
і тримає інваріанти §4 CLAUDE.md:
  * прев'ю растеризується з ТОГО САМОГО CanvasJSON, що йде у друк (один вектор);
  * друкарський файл із /api/render — вектор/CMYK (не растр);
  * шрифт віддається саме той .ttf, що бере рендер (той самий файл);
  * traversal-захист на /api/font.

Детермінізм: /api/brief тестуємо шорткатом len<3 (нейтральний дефолт без API),
решту — на готовому DesignBrief, тож жоден тест не палить токени/мережу.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.api import app

client = TestClient(app)


_BRIEF = {
    "style": "festive",
    "palette": ["#B00020", "#F5A623"],
    "text_elements": [
        {"content": "Вітаю зі святом!", "role": "title", "assumed": False},
        {"content": "Олена", "role": "name", "assumed": False},
    ],
    "layout_hint": "centered",
}


def _make_canvas(size: str = "a6") -> dict:
    resp = client.post("/api/canvas", json={"brief": _BRIEF, "size": size})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- /api/brief: шорткат порожнього вводу (без мережі) -----------------------


def test_brief_empty_prompt_uses_neutral_default_without_api() -> None:
    resp = client.post("/api/brief", json={"prompt": "  "})
    assert resp.status_code == 200
    brief = resp.json()["brief"]
    assert brief["style"] == "custom"
    assert brief["text_elements"] == []
    assert 1 <= len(brief["palette"]) <= 3


# --- /api/canvas: валідний CanvasJSON + кеш під id ---------------------------


def test_canvas_returns_valid_canvasjson_with_id() -> None:
    data = _make_canvas()
    assert "id" in data and data["id"]
    canvas = data["canvas"]
    assert canvas["version"] == "1.0"
    assert canvas["canvas"]["width_mm"] == 105.0  # a6
    # Той самий шрифт, що й у рендері (family/file з templater).
    assert canvas["fonts"][0]["file"] == "NotoSans-Regular.ttf"
    assert any(el["type"] == "text" for el in canvas["elements"])


def test_canvas_rejects_unknown_size() -> None:
    resp = client.post("/api/canvas", json={"brief": _BRIEF, "size": "poster"})
    assert resp.status_code == 400


# --- /api/preview: PNG з того самого вектора (§4 п.2) ------------------------


def test_preview_returns_png_from_same_canvas() -> None:
    canvas_id = _make_canvas()["id"]
    resp = client.get(f"/api/preview/{canvas_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG-сигнатура


def test_preview_unknown_id_404() -> None:
    assert client.get("/api/preview/deadbeef").status_code == 404


# --- /api/render: друкарський вектор/CMYK (§4 п.1-2, acceptance §4) ----------


def test_render_returns_cmyk_pdf_status_ok() -> None:
    canvas = _make_canvas()["canvas"]
    resp = client.post("/api/render", json=canvas)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    # Друкарський файл — CMYK, не растр (guardrail §4).
    assert b"/DeviceCMYK" in resp.content or b"CMYK" in resp.content


def test_render_rejects_invalid_canvas() -> None:
    resp = client.post("/api/render", json={"version": "1.0", "canvas": {}})
    assert resp.status_code == 400


# --- /api/font: той самий .ttf + traversal-захист ---------------------------


def test_font_serves_bundled_ttf() -> None:
    resp = client.get("/api/font/NotoSans-Regular.ttf")
    assert resp.status_code == 200
    assert resp.content[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")  # sfnt


def test_font_path_traversal_blocked() -> None:
    assert client.get("/api/font/../schema.py").status_code in (400, 404)
    assert client.get("/api/font/evil.txt").status_code == 400
