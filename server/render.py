"""JSON -> text-to-outlines -> векторний PDF -> Ghostscript CMYK/ICC (Фаза 0).

Тракт (guardrail §4 CLAUDE.md):
  * друкарський файл — вектор, ніколи не растр (растр лише у фото-зоні);
  * текст перетворюється у криві (outlines) на етапі складання PDF —
    жодних вбудованих шрифтів, отже зникає весь клас багів з кирилицею;
  * фінальний CMYK/PDF-X робить Ghostscript з ICC (модуль cmyk.py).

Публічна функція `render(canvas_json)` повертає готовий CMYK print.pdf (bytes).
`render_vector_pdf` повертає проміжний RGB-вектор (використовується у тестах
на інваріант «текст справді у кривих»).
"""

from __future__ import annotations

import io
import os
from functools import lru_cache

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from reportlab.lib.colors import CMYKColor, Color, HexColor
from reportlab.pdfgen import canvas as rl_canvas

from .cmyk import to_cmyk_pdf
from .schema import (
    CanvasJSON,
    CMYKColor as CMYKSpec,
    Color as ColorSpec,
    ImageElement,
    RectElement,
    RGBColor as RGBSpec,
    TextElement,
)

# Каталог із bundled .ttf. Той самий файл використовує і клієнт (інваріант).
FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

_MM_TO_PT = 72.0 / 25.4  # 1 мм у пунктах PostScript


def mm(value: float) -> float:
    """Міліметри -> пункти."""
    return value * _MM_TO_PT


# --- fontTools: гліф -> контур ReportLab -------------------------------------


class _ReportLabPen(BasePen):
    """Малює контур гліфа у ReportLab-path.

    BasePen сам розкладає qCurveTo (квадратичні криві TrueType) у кубічні
    `_curveToOne`, тож окремої конвертації quad->cubic не потрібно.
    Координати шрифта масштабуються `scale` і зсуваються у (dx, dy) — усе в pt.
    """

    def __init__(self, glyph_set, path, scale: float, dx: float, dy: float):
        super().__init__(glyph_set)
        self._path = path
        self._scale = scale
        self._dx = dx
        self._dy = dy

    def _pt(self, p):
        return (p[0] * self._scale + self._dx, p[1] * self._scale + self._dy)

    def _moveTo(self, p):
        x, y = self._pt(p)
        self._path.moveTo(x, y)

    def _lineTo(self, p):
        x, y = self._pt(p)
        self._path.lineTo(x, y)

    def _curveToOne(self, p1, p2, p3):
        x1, y1 = self._pt(p1)
        x2, y2 = self._pt(p2)
        x3, y3 = self._pt(p3)
        self._path.curveTo(x1, y1, x2, y2, x3, y3)

    def _closePath(self):
        self._path.close()


@lru_cache(maxsize=8)
def _load_font(file_name: str) -> TTFont:
    path = os.path.join(FONTS_DIR, file_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Шрифт не знайдено у server/fonts/: {file_name}")
    return TTFont(path)


def _rl_color(spec: ColorSpec) -> Color:
    """Colour-spec Canvas JSON -> об'єкт кольору ReportLab."""
    if isinstance(spec, RGBSpec):
        return HexColor(spec.rgb)
    if isinstance(spec, CMYKSpec):
        c, m_, y, k = spec.cmyk
        return CMYKColor(c, m_, y, k)
    raise TypeError(f"Невідомий тип кольору: {type(spec)!r}")


def _draw_text_outlines(
    c: rl_canvas.Canvas,
    el: TextElement,
    font: TTFont,
    canvas_h_pt: float,
) -> None:
    """Малює текст як векторні контури (не як текстовий об'єкт із шрифтом)."""
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    upem = font["head"].unitsPerEm
    hmtx = font["hmtx"]

    scale = el.size_pt / upem
    line_advance = el.size_pt * el.line_height

    # Anchor: (x_mm, y_mm) — базова лінія (baseline) першого рядка, верх-ліво.
    anchor_x = mm(el.x_mm)
    anchor_y = canvas_h_pt - mm(el.y_mm)

    c.saveState()
    c.translate(anchor_x, anchor_y)
    if el.rotation_deg:
        c.rotate(-el.rotation_deg)  # JSON — за годинниковою; PDF — проти.

    fill = _rl_color(el.fill)
    c.setFillColor(fill)

    for line_no, line in enumerate(el.text.split("\n")):
        # Ширина рядка для вирівнювання.
        widths = []
        for ch in line:
            gname = cmap.get(ord(ch))
            adv = hmtx[gname][0] if gname else upem // 2
            widths.append(adv * scale + el.letter_spacing)
        total_w = sum(widths)

        if el.align == "center":
            pen_x = -total_w / 2.0
        elif el.align == "right":
            pen_x = -total_w
        else:
            pen_x = 0.0
        pen_y = -line_no * line_advance

        for ch, adv_w in zip(line, widths):
            gname = cmap.get(ord(ch))
            if gname is not None and ch not in (" ",):
                path = c.beginPath()
                pen = _ReportLabPen(glyph_set, path, scale, pen_x, pen_y)
                glyph_set[gname].draw(pen)
                c.drawPath(path, fill=1, stroke=0)
            pen_x += adv_w

    c.restoreState()


def _draw_rect(c: rl_canvas.Canvas, el: RectElement, canvas_h_pt: float) -> None:
    anchor_x = mm(el.x_mm)
    anchor_y = canvas_h_pt - mm(el.y_mm)
    w = mm(el.width_mm)
    h = mm(el.height_mm)

    c.saveState()
    c.translate(anchor_x, anchor_y)
    if el.rotation_deg:
        c.rotate(-el.rotation_deg)

    fill_flag = 0
    if el.fill is not None:
        c.setFillColor(_rl_color(el.fill))
        fill_flag = 1
    stroke_flag = 0
    if el.stroke is not None and el.stroke_width_mm > 0:
        c.setStrokeColor(_rl_color(el.stroke))
        c.setLineWidth(mm(el.stroke_width_mm))
        stroke_flag = 1

    # Локальний прямокутник: верх-ліво в (0, 0), росте вниз -> низ у -h.
    if el.corner_radius_mm > 0:
        c.roundRect(0, -h, w, h, mm(el.corner_radius_mm), fill=fill_flag, stroke=stroke_flag)
    else:
        c.rect(0, -h, w, h, fill=fill_flag, stroke=stroke_flag)
    c.restoreState()


def _draw_image(c: rl_canvas.Canvas, el: ImageElement, canvas_h_pt: float) -> None:
    # Растр дозволений лише тут (схема гарантує is_photo_zone=True).
    anchor_x = mm(el.x_mm)
    anchor_y = canvas_h_pt - mm(el.y_mm)
    w = mm(el.width_mm)
    h = mm(el.height_mm)
    c.saveState()
    c.translate(anchor_x, anchor_y)
    if el.rotation_deg:
        c.rotate(-el.rotation_deg)
    c.drawImage(el.src, 0, -h, width=w, height=h, mask="auto")
    c.restoreState()


def render_vector_pdf(spec: CanvasJSON) -> bytes:
    """Складає векторний PDF (RGB/DeviceCMYK як задано), текст — у кривих.

    Це проміжний артефакт до Ghostscript. Тут уже гарантовано: жодних
    вбудованих шрифтів — увесь текст перетворено на контури.
    """
    total_w = mm(spec.canvas.width_mm + 2 * spec.canvas.bleed_mm)
    total_h = mm(spec.canvas.height_mm + 2 * spec.canvas.bleed_mm)
    buf = io.BytesIO()
    # pageCompression=0: проміжний артефакт лишаємо нестисненим — content-стрім
    # містить читабельні оператори (криві-заливки), без тексту. Фінальний
    # (gs) файл усе одно стискається у cmyk.py.
    c = rl_canvas.Canvas(buf, pagesize=(total_w, total_h), pageCompression=0)

    # Фон (враховуючи виліт).
    if spec.canvas.background is not None:
        c.setFillColor(_rl_color(spec.canvas.background))
        c.rect(0, 0, total_w, total_h, fill=1, stroke=0)

    # Зсув системи координат на величину вильоту: (0,0) JSON = кут trim-боксу.
    bleed_pt = mm(spec.canvas.bleed_mm)
    c.translate(bleed_pt, -bleed_pt)
    canvas_h_pt = total_h  # висота сторінки з вильотом

    for el in spec.elements:
        if isinstance(el, TextElement):
            font = _load_font(spec.font_file(el.font))
            _draw_text_outlines(c, el, font, canvas_h_pt)
        elif isinstance(el, RectElement):
            _draw_rect(c, el, canvas_h_pt)
        elif isinstance(el, ImageElement):
            _draw_image(c, el, canvas_h_pt)
        else:  # pragma: no cover — схема не допускає інших типів
            raise TypeError(f"Непідтримуваний елемент: {type(el)!r}")

    c.showPage()
    c.save()
    return buf.getvalue()


def render(spec: CanvasJSON) -> bytes:
    """Canvas JSON -> готовий друкарський print.pdf (вектор, CMYK/ICC/PDF-X).

    Крок 1: векторний PDF із текстом-у-кривих (render_vector_pdf).
    Крок 2: Ghostscript конвертує у CMYK/PDF-X з ICC-профілем (cmyk.to_cmyk_pdf).
    """
    rgb_pdf = render_vector_pdf(spec)
    return to_cmyk_pdf(rgb_pdf)


def render_preview_png(spec: CanvasJSON, *, dpi: int = 150) -> bytes:
    """Canvas JSON -> растровий PNG-прев'ю (тільки для екрану, guardrail §4 п.2).

    Растеризує ТОЙ САМИЙ векторний PDF (render_vector_pdf), що йде у друк, через
    той самий системний Ghostscript. Один рендер-шлях: прев'ю не має власної
    логіки малювання — це кадр із того ж вектора (той самий CanvasJSON, ті самі
    контури шрифту), тільки растр. Друкарський файл лишається вектором/CMYK.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    gs = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    if gs is None:
        raise RuntimeError(
            "Ghostscript не знайдено у PATH. Встанови: brew install ghostscript"
        )
    rgb_pdf = render_vector_pdf(spec)
    with tempfile.TemporaryDirectory(prefix="vektralogos_prev_") as tmp:
        in_pdf = os.path.join(tmp, "in.pdf")
        out_png = os.path.join(tmp, "out.png")
        with open(in_pdf, "wb") as fh:
            fh.write(rgb_pdf)
        cmd = [
            gs,
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=png16m",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            f"-r{dpi}",
            f"-sOutputFile={out_png}",
            in_pdf,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(out_png):
            raise RuntimeError(
                "Ghostscript (png-прев'ю) завершився з помилкою "
                f"(код {proc.returncode}).\nSTDERR:\n{proc.stderr}"
            )
        with open(out_png, "rb") as fh:
            return fh.read()
