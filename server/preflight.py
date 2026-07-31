"""Preflight — перевірка придатності до друку (спека §6 крок 6).

Функція, НЕ агент: приймає Canvas JSON (і опційно вихідний PDF), повертає звіт
`{ok, issues}`. Нічого не виправляє — авто-ремонт (vectorize, embed, upscale) це
Фаза 1 (preflight-агент). ok == немає жодної помилки рівня ERROR (WARN/INFO не
блокують).

Перевірки Фази 0:
  * bleed — чи достатній виліт (>= 3 мм);
  * фото-зони — ефективний DPI растра проти розміру розміщення;
  * CMYK — у вихідному PDF немає RGB-заливок (інваріант §4);
  * PDF/X — чи є ICC OutputIntent (інакше не повноцінний PDF/X);
  * межі — елементи не за межами медіабоксу (trim + bleed).
"""

from __future__ import annotations

import os
import re
import zlib
from dataclasses import dataclass, field
from typing import Literal

from .schema import CanvasJSON, ImageElement, RectElement, TextElement

Level = Literal["error", "warn", "info"]

_MM_PER_INCH = 25.4


@dataclass
class PreflightIssue:
    level: Level
    code: str
    message: str


@dataclass
class PreflightReport:
    ok: bool
    issues: list[PreflightIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issues": [
                {"level": i.level, "code": i.code, "message": i.message}
                for i in self.issues
            ],
        }


# --- допоміжне: розпакування content-стрімів PDF -----------------------------


def _is_texty(data: bytes) -> bool:
    """Чи схожий стрім на текстовий content (оператори), а не на бінарник.

    ICC-профіль, растрові зображення та шрифтові програми — бінарні; їх треба
    ВИКЛЮЧИТИ зі сканування кольорових операторів, інакше випадкові байти
    (напр. 'rg\\n' у ICC) дають хибні спрацювання.
    """
    if not data:
        return False
    printable = sum(1 for b in data if b in (9, 10, 13) or 32 <= b <= 126)
    return printable / len(data) > 0.85


def _pdf_content(pdf: bytes) -> bytes:
    """Конкатенація лише текстових (content) стрімів PDF, бінарні пропускаємо."""
    out = b""
    for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        body = raw.rstrip(b"\r\n")
        try:
            decoded = zlib.decompress(body)
        except zlib.error:
            decoded = body
        if _is_texty(decoded):
            out += b"\n" + decoded
    return out


def _image_pixel_size(src: str) -> tuple[int, int] | None:
    """Розмір растра у пікселях або None, якщо прочитати не вдалось."""
    if src.startswith(("http://", "https://")):
        return None  # віддалений — не тягнемо у Фазі 0
    if not os.path.exists(src):
        return None
    try:
        from reportlab.lib.utils import ImageReader

        return ImageReader(src).getSize()
    except Exception:  # noqa: BLE001 — будь-яка проблема читання = «не змогли»
        return None


# --- перевірки ---------------------------------------------------------------


def preflight(
    spec: CanvasJSON,
    pdf: bytes | None = None,
    *,
    min_dpi: int = 300,
    min_bleed_mm: float = 3.0,
) -> PreflightReport:
    """Повертає звіт про придатність до друку. Нічого не змінює."""
    issues: list[PreflightIssue] = []

    # 1. Bleed.
    if spec.canvas.bleed_mm < min_bleed_mm:
        issues.append(
            PreflightIssue(
                "warn",
                "bleed_too_small",
                f"bleed_mm={spec.canvas.bleed_mm} < рекомендованих {min_bleed_mm} мм",
            )
        )

    w, h, bleed = spec.canvas.width_mm, spec.canvas.height_mm, spec.canvas.bleed_mm

    for idx, el in enumerate(spec.elements):
        # 2. DPI фото-зон.
        if isinstance(el, ImageElement):
            size = _image_pixel_size(el.src)
            if size is None:
                issues.append(
                    PreflightIssue(
                        "info",
                        "image_unreadable",
                        f"elements[{idx}]: не вдалося прочитати растр '{el.src}' — DPI не перевірено",
                    )
                )
            else:
                px_w, px_h = size
                dpi_x = px_w / (el.width_mm / _MM_PER_INCH)
                dpi_y = px_h / (el.height_mm / _MM_PER_INCH)
                eff = min(dpi_x, dpi_y)
                if eff < min_dpi:
                    issues.append(
                        PreflightIssue(
                            "warn",
                            "low_dpi",
                            f"elements[{idx}]: фото-зона ~{eff:.0f} DPI < {min_dpi} "
                            f"({px_w}×{px_h}px на {el.width_mm}×{el.height_mm}мм)",
                        )
                    )

        # 3. Межі медіабоксу (trim + bleed). Координати від trim-кута.
        min_x, max_x = -bleed, w + bleed
        min_y, max_y = -bleed, h + bleed
        if isinstance(el, (RectElement, ImageElement)):
            x0, y0 = el.x_mm, el.y_mm
            x1, y1 = el.x_mm + el.width_mm, el.y_mm + el.height_mm
        else:  # TextElement — лише якір
            x0 = x1 = el.x_mm
            y0 = y1 = el.y_mm
        if x0 < min_x or y0 < min_y or x1 > max_x or y1 > max_y:
            issues.append(
                PreflightIssue(
                    "warn",
                    "out_of_media",
                    f"elements[{idx}] ({el.type}) виходить за медіабокс "
                    f"[{min_x}..{max_x}]×[{min_y}..{max_y}] мм",
                )
            )

    # 4/5. Перевірки вихідного PDF (якщо переданий).
    if pdf is not None:
        if not pdf.startswith(b"%PDF"):
            issues.append(PreflightIssue("error", "not_pdf", "Вихід не схожий на PDF"))
        else:
            content = _pdf_content(pdf)
            # RGB-заливки/обводки у друкарському файлі — порушення інваріанту CMYK.
            if re.search(rb"\brg[\r\n]", content) or re.search(rb"\bRG[\r\n]", content):
                issues.append(
                    PreflightIssue(
                        "error",
                        "rgb_in_print",
                        "У вихідному PDF є RGB-заливки (op rg/RG) — має бути CMYK",
                    )
                )
            # PDF/X OutputIntent (ICC) — інакше CMYK є, але не повноцінний PDF/X.
            if b"GTS_PDFX" not in pdf or b"OutputIntent" not in pdf:
                issues.append(
                    PreflightIssue(
                        "warn",
                        "no_pdfx_intent",
                        "Немає PDF/X OutputIntent (ICC) — задай PRINT_ICC_PROFILE",
                    )
                )

    ok = not any(i.level == "error" for i in issues)
    return PreflightReport(ok=ok, issues=issues)
