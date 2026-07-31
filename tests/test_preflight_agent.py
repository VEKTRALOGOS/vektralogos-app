"""Тести preflight-агента (Фаза 1, спека §7 acceptance criteria).

Петля не залежить від Ghostscript: fix-и рівня спеки, а фінальний рендер
інжектимо через `renderer=` (fake), крім тесту rgb_in_print, де свідомо
рендеримо проміжний RGB-PDF (render_vector_pdf, без gs).
"""

from __future__ import annotations

import os
import struct
import zlib

from server.preflight_agent import (
    clamp_to_media,
    fix_bleed,
    preflight_agent,
    shrink_photo_zone,
)
from server.render import render_vector_pdf
from server.schema import CanvasJSON

# --- хелпери -----------------------------------------------------------------


def _make_png(path: str, w: int, h: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


def _spec(bleed_mm: float = 3.0, elements: list | None = None) -> CanvasJSON:
    return CanvasJSON.model_validate(
        {
            "version": "1.0",
            "canvas": {"width_mm": 90, "height_mm": 50, "bleed_mm": bleed_mm},
            "fonts": [{"family": "Noto Sans", "file": "NotoSans-Regular.ttf"}],
            "elements": elements or [],
        }
    )


def _fake_pdf(_spec_arg: CanvasJSON) -> bytes:
    """Мінімальний «чистий» PDF (без RGB-операторів) — щоб не тягнути gs у тест."""
    return b"%PDF-1.4\n% clean cmyk stub\n%%EOF\n"


def _hello() -> CanvasJSON:
    path = os.path.join(os.path.dirname(__file__), "..", "examples", "hello.json")
    with open(path, encoding="utf-8") as fh:
        return CanvasJSON.model_validate_json(fh.read())


def _codes(report) -> set[str]:
    return {i.code for i in report.issues}


# --- fix-інструменти окремо (acceptance #4) ----------------------------------


def test_fix_bleed_raises_to_min_and_is_pure():
    spec = _spec(bleed_mm=1.0)
    out = fix_bleed(spec, 3.0)
    assert out.canvas.bleed_mm == 3.0
    assert spec.canvas.bleed_mm == 1.0  # вхід не змінено (чиста функція)


def test_fix_bleed_never_lowers():
    out = fix_bleed(_spec(bleed_mm=5.0), 3.0)
    assert out.canvas.bleed_mm == 5.0


def test_clamp_to_media_pulls_element_inside():
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"rgb": "#000000"}}
    spec = _spec(elements=[el])  # 80+40=120 > 90+3
    out = clamp_to_media(spec, 0)
    r = out.elements[0]
    assert r.x_mm + r.width_mm <= 90 + 3 + 1e-9
    assert r.width_mm == 40  # розмір збережено
    assert spec.elements[0].x_mm == 80  # вхід не змінено


def test_shrink_photo_zone_reaches_min_dpi(tmp_path):
    img = str(tmp_path / "small.png")
    _make_png(img, 50, 50)  # 50px
    el = {"type": "image", "x_mm": 5, "y_mm": 5, "width_mm": 25, "height_mm": 25,
          "src": img, "is_photo_zone": True}
    spec = _spec(elements=[el])
    out = shrink_photo_zone(spec, 0, min_dpi=300)
    e = out.elements[0]
    eff = min(50 / (e.width_mm / 25.4), 50 / (e.height_mm / 25.4))
    assert eff >= 300
    assert abs(e.width_mm / e.height_mm - 1.0) < 1e-6  # aspect ratio збережено
    assert spec.elements[0].width_mm == 25  # вхід не змінено


# --- петля: збіжність (acceptance #1) ----------------------------------------


def test_agent_converges_to_ok(tmp_path):
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(bleed_mm=1.0, elements=[el])  # bleed_too_small + out_of_media
    result = preflight_agent(spec, renderer=_fake_pdf)
    assert result.status == "ok"
    assert result.iterations <= 3
    assert result.spec.canvas.bleed_mm == 3.0
    r = result.spec.elements[0]
    assert r.x_mm + r.width_mm <= 90 + 3 + 1e-9


# --- петля: non-fixable error -> needs_human (acceptance #2) ------------------


def test_agent_rgb_in_print_needs_human():
    spec = _hello()  # чистий на рівні спеки
    # рендеримо проміжний RGB-PDF (є білий фон rg) -> rgb_in_print (error)
    result = preflight_agent(spec, renderer=render_vector_pdf)
    assert result.status == "needs_human"
    assert "rgb_in_print" in _codes(result.report)
    # агент нічого не «вигадав»: спека не мінялась
    assert result.spec.model_dump() == spec.model_dump()


# --- петля: конфлікт -> no_progress (acceptance #3) --------------------------


def test_agent_unfittable_element_stops_no_progress():
    # елемент ширший за медіабокс -> clamp не може влізти, issue повертається
    el = {"type": "rect", "x_mm": 0, "y_mm": 0, "width_mm": 200, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(elements=[el])  # 200 > 90+2*3
    result = preflight_agent(spec, renderer=_fake_pdf)
    assert result.status == "no_progress"
    assert "out_of_media" in _codes(result.report)


def test_agent_max_iterations_guard(tmp_path):
    # форсуємо жорсткий запобіжник малим max_iterations на реальному фіксі
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(bleed_mm=1.0, elements=[el])
    result = preflight_agent(spec, renderer=_fake_pdf, max_iterations=1)
    assert result.status == "max_iterations_reached"
    assert result.iterations == 1
