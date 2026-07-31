"""Тести на тверді інваріанти §4 CLAUDE.md.

Кожен інваріант — окремий тест: «текст справді у кривих», «PDF справді CMYK»,
«растр лише у фото-зоні», єдине джерело правди (одна схема).
"""

from __future__ import annotations

import os
import re
import shutil
import zlib

import pytest

from server.render import render, render_vector_pdf
from server.schema import CanvasJSON

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "hello.json")


def _stream_bodies(pdf: bytes) -> bytes:
    """Сирі тіла стрімів (для нестисненого проміжного PDF)."""
    return b"".join(re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S))


def _decompressed_streams(pdf: bytes) -> bytes:
    """Конкатенація розпакованих FlateDecode-стрімів PDF.

    Оператори кольору та малювання у фінальному (gs) PDF живуть у стиснених
    content-стрімах, тож перевіряти інваріанти треба на розпакованому вмісті.
    """
    out = b""
    for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        try:
            out += zlib.decompress(raw.rstrip(b"\r\n"))
        except zlib.error:
            out += raw  # нестиснений стрім
    return out


@pytest.fixture(scope="module")
def hello_spec() -> CanvasJSON:
    with open(EXAMPLE, "r", encoding="utf-8") as fh:
        return CanvasJSON.model_validate_json(fh.read())


# --- Інваріант: єдине джерело правди (одна схема валідує ввід) ----------------


def test_example_validates_against_schema(hello_spec: CanvasJSON):
    assert hello_spec.version == "1.0"
    assert hello_spec.canvas.width_mm == 90
    # font_file резолвиться, бо family оголошено у fonts.
    assert hello_spec.font_file("Noto Sans") == "NotoSans-Regular.ttf"


def test_unknown_font_family_raises(hello_spec: CanvasJSON):
    with pytest.raises(KeyError):
        hello_spec.font_file("Comic Sans")


# --- Інваріант: текст справді у кривих (жодних вбудованих шрифтів) ------------


def test_text_is_outlined_no_embedded_fonts(hello_spec: CanvasJSON):
    pdf = render_vector_pdf(hello_spec)
    assert pdf.startswith(b"%PDF")
    # Жодного вбудованого шрифта: немає програми шрифта і Type0/TrueType-словників.
    # (ReportLab лишає невживане base-14 посилання без /FontFile — гліфів у ньому нема.)
    assert b"/FontFile" not in pdf, "Не має бути вбудованого шрифта"
    assert b"/Type0" not in pdf
    assert b"/TrueType" not in pdf
    # Головне: у (нестисненому) content-стрімі немає ЖОДНОГО оператора ПОКАЗУ
    # тексту (Tj/TJ/'/"). ReportLab лишає порожній BT..ET, що лише виставляє
    # стан шрифта і нічого не малює — гліфи ж намальовано як криві (c ... f*).
    content = _stream_bodies(pdf)
    for show_op in (b" Tj", b" TJ", b"' ", b'" '):
        assert show_op not in content, f"Знайдено оператор показу тексту {show_op!r}"
    assert re.search(rb"\bf\*?[\r\n]", content), "Мають бути заливки контурів (op `f`/`f*`)"


# --- Інваріант: растрові зображення лише у явній фото-зоні --------------------


def test_image_without_photo_zone_flag_is_rejected():
    bad = {
        "version": "1.0",
        "canvas": {"width_mm": 50, "height_mm": 50},
        "fonts": [],
        "elements": [
            {
                "type": "image",
                "x_mm": 0,
                "y_mm": 0,
                "width_mm": 10,
                "height_mm": 10,
                "src": "photo.png",
                "is_photo_zone": False,
            }
        ],
    }
    with pytest.raises(Exception):  # ValidationError: is_photo_zone мусить бути true
        CanvasJSON.model_validate(bad)


def test_image_missing_photo_zone_flag_is_rejected():
    bad = {
        "version": "1.0",
        "canvas": {"width_mm": 50, "height_mm": 50},
        "elements": [
            {
                "type": "image",
                "x_mm": 0,
                "y_mm": 0,
                "width_mm": 10,
                "height_mm": 10,
                "src": "photo.png",
            }
        ],
    }
    with pytest.raises(Exception):
        CanvasJSON.model_validate(bad)


# --- Інваріант: друкарський PDF справді CMYK (Ghostscript + ICC) --------------


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript не встановлено")
def test_render_output_is_cmyk(hello_spec: CanvasJSON):
    pdf = render(hello_spec)
    assert pdf.startswith(b"%PDF")
    content = _decompressed_streams(pdf)
    # Заливки у content-стрімі — оператором CMYK `k`, і жодного RGB-оператора `rg`.
    assert re.search(rb"\bk[\r\n]", content), "Друкарський PDF має заливки у CMYK (op `k`)"
    assert not re.search(rb"\brg[\r\n]", content), "У CMYK-файлі не має лишатися RGB (op `rg`)"
    # І текст усе ще у кривих після gs.
    assert b"/FontFile" not in pdf
