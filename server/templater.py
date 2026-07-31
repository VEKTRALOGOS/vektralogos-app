"""Детермінований шаблонизатор: DesignBrief -> Canvas JSON.

Це «міст» гібридного контракту Фази 0: семантичний бриф від LLM
перетворюється на конкретну раскладку з міліметровими координатами БЕЗ участі
моделі. Політика проста і передбачувана (Фаза 0):

  * порядок і кегль — за роллю (title, name, message, date);
  * вирівнювання й вертикальний якір — за layout_hint;
  * колір тексту — найтемніший колір палітри (fallback #1A1A1A);
  * фон — білий (безпечно для друку).

Складніша типографіка/шаблони — пізніші фази.
"""

from __future__ import annotations

from .brief import DesignBrief
from .schema import Canvas, CanvasJSON, FontRef, RGBColor, TextElement

_PT_TO_MM = 25.4 / 72.0
_LINE_HEIGHT = 1.25

# Порядок стека зверху вниз і кегль (pt) за роллю.
_ROLE_ORDER = {"title": 0, "name": 1, "message": 2, "date": 3}
_ROLE_SIZE_PT = {"title": 16.0, "name": 28.0, "message": 13.0, "date": 11.0}

DEFAULT_FONT_FAMILY = "Noto Sans"
DEFAULT_FONT_FILE = "NotoSans-Regular.ttf"


def _luminance(hex_color: str) -> float:
    r = int(hex_color[1:3], 16) / 255.0
    g = int(hex_color[3:5], 16) / 255.0
    b = int(hex_color[5:7], 16) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _text_color(palette: list[str]) -> RGBColor:
    """Найтемніший колір палітри; якщо всі світлі — темно-сірий дефолт."""
    darkest = min(palette, key=_luminance)
    if _luminance(darkest) > 0.6:
        return RGBColor(rgb="#1A1A1A")
    return RGBColor(rgb=darkest.upper())


def brief_to_canvas(
    brief: DesignBrief,
    *,
    width_mm: float,
    height_mm: float,
    bleed_mm: float = 3.0,
    font_family: str = DEFAULT_FONT_FAMILY,
    font_file: str = DEFAULT_FONT_FILE,
) -> CanvasJSON:
    """Перетворює DesignBrief на валідний Canvas JSON із раскладкою."""
    fill = _text_color(brief.palette)
    align = "center" if brief.layout_hint in ("centered", "banner") else "left"
    margin = max(bleed_mm, 6.0)

    # Пропускаємо порожній вміст; сортуємо за роллю для осмисленого стека.
    items = [t for t in brief.text_elements if t.content.strip()]
    items.sort(key=lambda t: _ROLE_ORDER.get(t.role, 99))

    # Висоти рядків для вертикального центрування.
    sizes_mm = [_ROLE_SIZE_PT[t.role] * _PT_TO_MM for t in items]
    gaps = [s * (_LINE_HEIGHT - 1.0) for s in sizes_mm]
    block_h = sum(s * _LINE_HEIGHT for s in sizes_mm)

    if brief.layout_hint == "centered":
        cursor_top = max(margin, (height_mm - block_h) / 2.0)
    else:  # left-aligned / banner / corner — від верхнього поля
        cursor_top = margin

    x = width_mm / 2.0 if align == "center" else margin

    elements: list = []
    for t, size_mm, gap in zip(items, sizes_mm, gaps):
        ascent = 0.8 * size_mm
        baseline_y = cursor_top + ascent
        elements.append(
            TextElement(
                x_mm=round(x, 2),
                y_mm=round(baseline_y, 2),
                text=t.content,
                font=font_family,
                size_pt=_ROLE_SIZE_PT[t.role],
                fill=fill,
                align=align,
            )
        )
        cursor_top += size_mm * _LINE_HEIGHT + gap

    return CanvasJSON(
        version="1.0",
        canvas=Canvas(
            width_mm=width_mm,
            height_mm=height_mm,
            bleed_mm=bleed_mm,
            dpi=300,
            background=RGBColor(rgb="#FFFFFF"),
        ),
        fonts=[FontRef(family=font_family, file=font_file)],
        elements=elements,
    )
