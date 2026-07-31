"""Тести preflight-функції (спека §6 крок 6)."""

from __future__ import annotations

import os
import shutil
import struct
import zlib

import pytest

from server.preflight import preflight
from server.render import render, render_vector_pdf
from server.schema import CanvasJSON


def _make_png(path: str, w: int, h: int) -> None:
    """Мінімальний валідний PNG w×h (суцільний колір) — для DPI-перевірки."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-біт RGB
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))  # фільтр0 + пікселі
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
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


def _codes(report) -> set[str]:
    return {i.code for i in report.issues}


# --- bleed -------------------------------------------------------------------


def test_small_bleed_warns_but_ok():
    r = preflight(_spec(bleed_mm=0))
    assert "bleed_too_small" in _codes(r)
    assert r.ok  # warn не блокує


# --- межі медіабоксу ---------------------------------------------------------


def test_element_out_of_media_warns():
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"rgb": "#000000"}}
    r = preflight(_spec(elements=[el]))  # 80+40=120 > 90+3
    assert "out_of_media" in _codes(r)


# --- фото-зона DPI -----------------------------------------------------------


def test_low_dpi_photo_zone_warns(tmp_path):
    img = str(tmp_path / "small.png")
    _make_png(img, 50, 50)  # 50px на 25мм ≈ 51 DPI
    el = {"type": "image", "x_mm": 5, "y_mm": 5, "width_mm": 25, "height_mm": 25,
          "src": img, "is_photo_zone": True}
    r = preflight(_spec(elements=[el]))
    assert "low_dpi" in _codes(r)
    assert r.ok  # це warn, не error


def test_remote_image_cannot_be_verified():
    el = {"type": "image", "x_mm": 5, "y_mm": 5, "width_mm": 25, "height_mm": 25,
          "src": "https://example.com/p.jpg", "is_photo_zone": True}
    r = preflight(_spec(elements=[el]))
    assert "image_unreadable" in _codes(r)


# --- CMYK / PDF-X у вихідному PDF ---------------------------------------------


def _hello() -> CanvasJSON:
    path = os.path.join(os.path.dirname(__file__), "..", "examples", "hello.json")
    with open(path, encoding="utf-8") as fh:
        return CanvasJSON.model_validate_json(fh.read())


def test_rgb_intermediate_pdf_flagged_as_error():
    spec = _hello()
    rgb_pdf = render_vector_pdf(spec)  # проміжний RGB (є білий фон rg)
    r = preflight(spec, rgb_pdf)
    assert "rgb_in_print" in _codes(r)
    assert not r.ok  # RGB у друкарському файлі = error


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript не встановлено")
def test_cmyk_output_has_no_rgb_error(monkeypatch):
    monkeypatch.delenv("PRINT_ICC_PROFILE", raising=False)
    spec = _hello()
    pdf = render(spec)  # без ICC -> CMYK, але не PDF/X
    r = preflight(spec, pdf)
    assert "rgb_in_print" not in _codes(r)
    assert "no_pdfx_intent" in _codes(r)  # без ICC -> попередження
    assert r.ok  # лише warn


ICC = os.path.join(os.path.dirname(__file__), "..", "server", "icc", "ISOcoated_v2_eci.icc")


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript не встановлено")
@pytest.mark.skipif(not os.path.exists(ICC), reason="ICC не завантажено (make fetch-icc)")
def test_pdfx_output_is_clean(monkeypatch):
    monkeypatch.setenv("PRINT_ICC_PROFILE", ICC)
    spec = _hello()
    pdf = render(spec)  # PDF/X з ICC
    r = preflight(spec, pdf)
    assert r.ok
    assert _codes(r) == set() or _codes(r) <= {"out_of_media"}  # чисто (крім можливих меж)
