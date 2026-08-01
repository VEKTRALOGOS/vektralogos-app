"""Офлайн-тести онбординг-флоу (TASKS #22), без LLM/мережі.

Ключове: бейджі та стадії експорту — з РЕАЛЬНОГО тракту, а не намальовані
(ґардрейл спеки onboarding-flow.md §«Залежності»: жодних фейкових таймерів).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.api import app
from server.onboarding import PRESETS, compute_badges, export_stages, preset_canvas
from server.preflight import preflight
from server.render import render
from server.schema import CanvasJSON, TextElement

client = TestClient(app)


# --- Пресети: 3 товари, кирилиця в кожному (доказ «з коробки») ----------------


def test_presets_have_cyrillic_in_every_sample() -> None:
    assert len(PRESETS) == 3
    for p in PRESETS:
        canvas = preset_canvas(p)
        texts = [e.text for e in canvas.elements if isinstance(e, TextElement)]
        joined = " ".join(texts)
        assert texts, f"пресет {p.id} без тексту"
        # Хоч один кириличний символ у прикладі.
        assert any("а" <= ch.lower() <= "я" or ch in "їієґ" for ch in joined), p.id


def test_api_presets_returns_three_with_canvas_and_preview_id() -> None:
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    presets = resp.json()["presets"]
    assert len(presets) == 3
    for p in presets:
        assert p["canvas"]["version"] == "1.0"
        assert "preview_id" in p
    # preview_id справді резолвиться у растр (той самий вектор).
    prev = client.get(f"/api/preview/{presets[0]['preview_id']}")
    assert prev.status_code == 200
    assert prev.headers["content-type"] == "image/png"


# --- Стадії експорту: реальні фази у правильному порядку ----------------------


def test_export_stages_order_and_real_badges() -> None:
    spec = preset_canvas(PRESETS[0])
    stages = []
    done = None
    for stage, payload in export_stages(spec):
        if stage == "done":
            done = payload
        else:
            stages.append(stage)
    assert stages == ["converting_cmyk", "checking_dpi"]
    assert done is not None and done["ok"] is True
    # PDF у done — справжній друкарський файл.
    assert done["pdf"].startswith(b"%PDF")
    labels = {b["label"]: b["ok"] for b in done["badges"]}
    assert labels == {"CMYK": True, "300 DPI": True, "Текст у кривих": True, "Вильоти": True}


def test_badges_reflect_real_report_not_hardcoded() -> None:
    """Зіпсований spec (малий bleed) → бейдж «Вильоти» стає False чесно."""
    spec = preset_canvas(PRESETS[0])
    bad = spec.model_copy(deep=True)
    bad.canvas.bleed_mm = 0.0
    pdf = render(bad)
    report = preflight(bad, pdf)
    badges = {b["label"]: b["ok"] for b in compute_badges(bad, pdf, report)}
    assert badges["Вильоти"] is False   # bleed_too_small спрацював
    assert badges["CMYK"] is True       # CMYK усе одно валідний


# --- SSE-ендпоінт + завантаження ---------------------------------------------


def test_export_stream_emits_sse_and_download_works() -> None:
    canvas = preset_canvas(PRESETS[1]).model_dump()
    resp = client.post("/api/export/stream", json=canvas)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: converting_cmyk" in body
    assert "event: checking_dpi" in body
    assert "event: done" in body

    # Витягуємо download_url із done-події, тягнемо PDF.
    import json
    import re

    m = re.search(r"event: done\ndata: (.*)", body)
    assert m
    data = json.loads(m.group(1))
    dl = client.get(data["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.content.startswith(b"%PDF")


def test_download_unknown_id_404() -> None:
    assert client.get("/api/download/nope").status_code == 404


def test_onboarding_page_served() -> None:
    resp = client.get("/onboarding")
    assert resp.status_code == 200
    assert "Vektralogos" in resp.text
