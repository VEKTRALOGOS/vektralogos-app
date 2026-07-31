"""Офлайн-тести гібридного контракту (без виклику API).

Перевіряємо DesignBrief-валідацію, нейтральний дефолт/шорткат, і що
шаблонизатор дає валідний Canvas JSON, який реально рендериться у вектор.
"""

from __future__ import annotations

import pytest

from server.brief import BriefTextElement, DesignBrief, neutral_default_brief, prompt_to_brief
from server.render import render_vector_pdf
from server.templater import brief_to_canvas


def _sample_brief() -> DesignBrief:
    return DesignBrief(
        style="festive",
        palette=["#12305A", "#D4AF37"],
        text_elements=[
            BriefTextElement(content="Іван", role="name"),
            BriefTextElement(content="З Днем Народження!", role="message"),
            BriefTextElement(content="14 червня 2027", role="date", assumed=True),
        ],
        layout_hint="centered",
    )


# --- DesignBrief валідація ----------------------------------------------------


def test_bad_role_rejected():
    with pytest.raises(Exception):
        DesignBrief(
            style="x", palette=["#000000"], layout_hint="centered",
            text_elements=[{"content": "a", "role": "slogan"}],
        )


def test_non_hex_palette_rejected():
    with pytest.raises(Exception):
        DesignBrief(style="x", palette=["blue"], layout_hint="centered")


def test_palette_size_bounds():
    with pytest.raises(Exception):  # >3 кольори
        DesignBrief(
            style="x",
            palette=["#000000", "#111111", "#222222", "#333333"],
            layout_hint="centered",
        )


# --- Шорткат / нейтральний дефолт (без API) ----------------------------------


def test_empty_prompt_shortcut_returns_neutral_without_api():
    # len<3 -> нейтральний дефолт, жодного мережевого виклику.
    brief = prompt_to_brief("")
    assert brief.style == "custom"
    assert brief.text_elements == []
    assert brief.layout_hint == "centered"


def test_neutral_default_is_valid():
    b = neutral_default_brief()
    assert 1 <= len(b.palette) <= 3


# --- Шаблонизатор -> валідний, рендериться ------------------------------------


def test_templater_produces_renderable_canvas():
    spec = brief_to_canvas(_sample_brief(), width_mm=105, height_mm=148)
    # Шрифт оголошено, family резолвиться.
    assert spec.font_file("Noto Sans") == "NotoSans-Regular.ttf"
    # Усі текстові елементи в межах полотна.
    for el in spec.elements:
        assert 0 <= el.x_mm <= spec.canvas.width_mm
        assert 0 <= el.y_mm <= spec.canvas.height_mm
    # Центрована раскладка -> вирівнювання по центру.
    assert all(el.align == "center" for el in spec.elements)
    # І це реально рендериться у вектор (текст у кривих).
    pdf = render_vector_pdf(spec)
    assert pdf.startswith(b"%PDF")
    assert b"/FontFile" not in pdf


def test_left_aligned_hint_maps_to_left():
    b = _sample_brief()
    b.layout_hint = "left-aligned"
    spec = brief_to_canvas(b, width_mm=90, height_mm=50)
    assert all(el.align == "left" for el in spec.elements)


def test_empty_text_elements_render_blank_canvas():
    spec = brief_to_canvas(neutral_default_brief(), width_mm=90, height_mm=50)
    assert spec.elements == []
    pdf = render_vector_pdf(spec)
    assert pdf.startswith(b"%PDF")
