"""Онбординг-флоу (спека docs/specs/onboarding-flow.md, TASKS #22).

Логіка для 7-крокового флоу поверх готового тракту Ф0–Ф1 — БЕЗ нового
pipeline і БЕЗ фейкових таймерів (спека §«Залежності», рядок 49-52):

  * Пресети (крок 3) — детерміновані DesignBrief → brief_to_canvas, з кирилицею
    в кожному прикладі (головний доказ «кирилиця з коробки»). Без виклику LLM.
  * Стадії експорту (крок 5) — РЕАЛЬНІ фази тракту: `render_vector_pdf` →
    `to_cmyk_pdf` (це і є converting_cmyk) → `preflight` (це і є checking_dpi).
    Мітка стадії відповідає роботі, що реально відбувається, а не таймеру.
  * Бейджі (крок 6) — виведені з РЕАЛЬНОГО preflight-звіту та байтів PDF, а не
    намальовані галочки. `CMYK` = немає rgb_in_print; `300 DPI` = немає low_dpi;
    `текст у кривих` = у PDF немає /BaseFont (рендер завжди робить outlines);
    `вильоти` = немає bleed_too_small.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from .brief import BriefTextElement, DesignBrief
from .preflight import PreflightReport, preflight
from .prompt_to_canvas import brief_from_prompt_to_canvas
from .render import render_vector_pdf
from .cmyk import to_cmyk_pdf
from .schema import CanvasJSON


# --- Пресети (крок 3): 3 товари, у кожному кирилиця ---------------------------


@dataclass(frozen=True)
class Preset:
    id: str
    title: str          # людська назва в пікері
    product: str        # футболка / гравіювання / табличка
    size: str           # розмір полотна (paper size)
    brief: DesignBrief


def _brief(style: str, palette: list[str], texts: list[tuple[str, str]],
           layout: str) -> DesignBrief:
    return DesignBrief(
        style=style,
        palette=palette,
        text_elements=[BriefTextElement(content=c, role=r) for c, r in texts],
        layout_hint=layout,  # type: ignore[arg-type]
    )


# Кирилиця в КОЖНОМУ пресеті — це і є доказ, що працює з коробки.
PRESETS: list[Preset] = [
    Preset(
        id="tshirt",
        title="Футболка — подарунок",
        product="футболка",
        size="a5",
        brief=_brief(
            "festive", ["#B00020", "#F5A623"],
            [("Найкраща у світі мама", "title"), ("Олена", "name")],
            "centered",
        ),
    ),
    Preset(
        id="engraving",
        title="Гравіювання — брелок",
        product="гравіювання",
        size="card",
        brief=_brief(
            "minimal", ["#1A1A1A", "#8A8A8A"],
            [("З любов'ю", "title"), ("14.06.2026", "date")],
            "centered",
        ),
    ),
    Preset(
        id="plate",
        title="Табличка — вітальна",
        product="табличка",
        size="a6",
        brief=_brief(
            "formal", ["#0B3D2E", "#C8A24B"],
            [("Ласкаво просимо", "title"), ("Родина Ковальчук", "name")],
            "centered",
        ),
    ),
]


def preset_canvas(preset: Preset) -> CanvasJSON:
    """Детермінований CanvasJSON пресету (без LLM)."""
    return brief_from_prompt_to_canvas(preset.brief, size=preset.size)


def presets_payload() -> list[dict[str, Any]]:
    """Список пресетів для пікера: метадані + готовий CanvasJSON кожного."""
    out: list[dict[str, Any]] = []
    for p in PRESETS:
        out.append({
            "id": p.id,
            "title": p.title,
            "product": p.product,
            "size": p.size,
            "canvas": preset_canvas(p).model_dump(),
        })
    return out


# --- Бейджі (крок 6): виведені з реального звіту + байтів PDF -----------------


def compute_badges(spec: CanvasJSON, pdf: bytes, report: PreflightReport) -> list[dict[str, Any]]:
    """Чесні бейджі: кожен — факт із preflight-звіту / байтів PDF, не малюнок."""
    codes = {i.code for i in report.issues}

    # CMYK за авторитетом preflight: немає RGB-операторів (rg/RG) у друкарському
    # PDF. Саме так проєкт визначає інваріант CMYK — не за наявністю рядка
    # /DeviceCMYK (gs конвертує кольори в оператори k/K без такого ресурсу).
    cmyk_ok = "rgb_in_print" not in codes and "not_pdf" not in codes
    dpi_ok = "low_dpi" not in codes and spec.canvas.dpi >= 300
    # Рендер завжди перетворює текст у криві → у PDF немає програм шрифтів.
    outlines_ok = b"/BaseFont" not in pdf and b"/FontFile" not in pdf
    bleed_ok = "bleed_too_small" not in codes and spec.canvas.bleed_mm >= 3.0

    return [
        {"key": "cmyk", "ok": cmyk_ok, "label": "CMYK",
         "detail": "DeviceCMYK, без RGB-заливок"},
        {"key": "dpi", "ok": dpi_ok, "label": "300 DPI",
         "detail": f"полотно {spec.canvas.dpi} DPI, фото-зони в нормі"},
        {"key": "outlines", "ok": outlines_ok, "label": "Текст у кривих",
         "detail": "гліфи як контури — без вбудованих шрифтів"},
        {"key": "bleed", "ok": bleed_ok, "label": "Вильоти",
         "detail": f"bleed {spec.canvas.bleed_mm} мм"},
    ]


# --- Стадії експорту (крок 5): реальні фази тракту ---------------------------


def export_stages(spec: CanvasJSON) -> Iterator[tuple[str, dict[str, Any]]]:
    """Генерує (stage, payload) уздовж РЕАЛЬНИХ фаз рендеру.

    Порядок міток збігається з роботою, яка відбувається одразу після мітки:
      converting_cmyk → (render_vector_pdf + to_cmyk_pdf) → checking_dpi →
      (preflight) → done (з бейджами + байтами PDF у payload). Жодних штучних
      затримок: якщо gs відпрацював швидко — стадія просто швидка (чесно).
    """
    yield "converting_cmyk", {"message": "Конвертація в CMYK…"}
    vector = render_vector_pdf(spec)
    pdf = to_cmyk_pdf(vector)

    yield "checking_dpi", {"message": "Перевірка DPI…"}
    report = preflight(spec, pdf)
    badges = compute_badges(spec, pdf, report)

    yield "done", {
        "ok": report.ok,
        "badges": badges,
        "pdf": pdf,  # байти; шар API збереже під id і віддасть URL
        "issues": [
            {"level": i.level, "code": i.code, "message": i.message}
            for i in report.issues
        ],
    }
