"""Preflight-агент — ReAct-петля навколо preflight() (Фаза 1, STRATEGY §3).

Різниця з Фазою 0: `preflight()` лише ЗВІТУЄ. Агент читає звіт, для кожного
виправного issue-коду обирає детермінований fix-інструмент, застосовує його,
перевіряє знову — і так по колу, поки дизайн не стане придатним до друку або
поки лишаться тільки проблеми, які редагування Canvas JSON не чинить.

Свідомі рішення (див. docs/specs/VEKTRALOGOS_Phase1_Plan-and-Decisions):
  * У петлі НЕМАЄ виклику LLM — вибір інструмента 1:1 за кодом issue.
    Детермінізм тут риса, не хиба: друкарський фікс не віддають на «вгадування».
  * Fix-и — чисті функції, повертають НОВИЙ CanvasJSON (model_copy), не мутують
    вхід. Юніт-тестуються окремо від петлі.
  * Виправні коди — рівня спеки (bleed/розмір/DPI), тому петля гоняє
    preflight(spec) БЕЗ рендеру PDF. Ghostscript-рендер робиться один раз у
    кінці, для фінального звіту (RGB/PDF-X перевірки).
  * Зупинка: фікспойнт (набір issue перестав зменшуватись → конфлікт), або
    жорсткий запобіжник max_iterations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .preflight import PreflightIssue, PreflightReport, _MM_PER_INCH, _image_pixel_size, preflight
from .render import render
from .schema import CanvasJSON, ImageElement, RectElement

AgentStatus = Literal["ok", "needs_human", "no_progress", "max_iterations_reached"]


@dataclass
class AgentResult:
    """Результат прогону агента."""

    spec: CanvasJSON  # виправлений Canvas JSON (може дорівнювати входу)
    pdf: bytes  # фінальний рендер виправленого spec
    report: PreflightReport  # фінальний preflight (spec + pdf)
    iterations: int  # скільки ітерацій пройдено
    status: AgentStatus


# --- Чисті fix-інструменти (тестуються окремо від петлі, acceptance §7 #4) ----


def fix_bleed(spec: CanvasJSON, min_bleed_mm: float = 3.0) -> CanvasJSON:
    """`canvas.bleed_mm = max(поточний, min_bleed_mm)`. Один issue -> один виклик."""
    new = spec.model_copy(deep=True)
    new.canvas.bleed_mm = max(new.canvas.bleed_mm, min_bleed_mm)
    return new


def _clamp_axis(pos: float, size: float, lo: float, hi: float) -> float:
    """Найближча позиція, за якої [pos, pos+size] влазить у [lo, hi].

    Якщо size > (hi - lo) — влізти неможливо; повертаємо lo (best effort). Петля
    це помітить як відсутність прогресу і зупиниться зі статусом no_progress.
    """
    if pos < lo:
        pos = lo
    if pos + size > hi:
        pos = hi - size
    if pos < lo:  # size більший за доступний простір — не влазить
        pos = lo
    return pos


def clamp_to_media(spec: CanvasJSON, element_index: int) -> CanvasJSON:
    """Зсуває елемент так, щоб влізти в медіабокс [-bleed..w+bleed]×
    [-bleed..h+bleed], зберігаючи width/height. Текст (лише якір) — як точку."""
    new = spec.model_copy(deep=True)
    el = new.elements[element_index]
    w, h, bleed = new.canvas.width_mm, new.canvas.height_mm, new.canvas.bleed_mm
    if isinstance(el, (RectElement, ImageElement)):
        ew, eh = el.width_mm, el.height_mm
    else:  # TextElement — тільки якір
        ew = eh = 0.0
    el.x_mm = _clamp_axis(el.x_mm, ew, -bleed, w + bleed)
    el.y_mm = _clamp_axis(el.y_mm, eh, -bleed, h + bleed)
    return new


def shrink_photo_zone(
    spec: CanvasJSON, element_index: int, min_dpi: int = 300
) -> CanvasJSON:
    """Зменшує width_mm/height_mm фото-зони (зберігаючи aspect ratio, якір
    лишається) до розміру, за якого фактичний DPI растра >= min_dpi.

    НЕ чіпає сам файл зображення — лише розмір розміщення на полотні (свідоме
    рішення §3.3: не апскейлимо піксель = не вигадуємо дані на друк). Якщо
    растр прочитати не вдалось — повертає spec без змін (це non-fixable кейс).

    Відомий ліміт Фази 1 (рішення Антона): overlap-check не робимо. Усадка йде
    до якоря (верх-ліво), нових накладань не створює, але може лишити візуальну
    «дірку». Композиційна перевірка — Фаза 2 (LangGraph).
    """
    new = spec.model_copy(deep=True)
    el = new.elements[element_index]
    size = _image_pixel_size(el.src)
    if size is None:
        return new  # растр не читається -> не наш кейс, лишаємо як є
    px_w, px_h = size
    max_w = px_w / min_dpi * _MM_PER_INCH
    max_h = px_h / min_dpi * _MM_PER_INCH
    # Рівномірний масштаб (aspect ratio), обмежений тим виміром, що впирається.
    # 1e-6 — захист від FP-межі, щоб eff вийшов строго >= min_dpi, а не 299.999.
    scale = min(max_w / el.width_mm, max_h / el.height_mm, 1.0) * (1 - 1e-6)
    el.width_mm = el.width_mm * scale
    el.height_mm = el.height_mm * scale
    return new


# --- Адаптери issue -> args (уся розпаковка issue живе тут, не у fix-логіці) ---

FixAdapter = Callable[..., CanvasJSON]


def _adapt_bleed(spec: CanvasJSON, issue: PreflightIssue, *, min_dpi: int,
                 min_bleed_mm: float) -> CanvasJSON:
    return fix_bleed(spec, min_bleed_mm)


def _adapt_clamp(spec: CanvasJSON, issue: PreflightIssue, *, min_dpi: int,
                 min_bleed_mm: float) -> CanvasJSON:
    assert issue.element_index is not None, "out_of_media без element_index"
    return clamp_to_media(spec, issue.element_index)


def _adapt_shrink(spec: CanvasJSON, issue: PreflightIssue, *, min_dpi: int,
                  min_bleed_mm: float) -> CanvasJSON:
    assert issue.element_index is not None, "low_dpi без element_index"
    return shrink_photo_zone(spec, issue.element_index, min_dpi)


# Явний allowlist: код -> адаптер. Невідомі коди агент не чіпає (§5).
FIXABLE_CODES: dict[str, FixAdapter] = {
    "bleed_too_small": _adapt_bleed,
    "out_of_media": _adapt_clamp,
    "low_dpi": _adapt_shrink,
}


# --- Петля -------------------------------------------------------------------


def _classify(report: PreflightReport) -> AgentStatus:
    """Фінальний статус за звітом (spec + pdf).

    needs_human лише коли лишився ERROR, якого агент не чинить (rgb_in_print,
    not_pdf) — це збій pipeline/оточення, не дизайну (§5). Нешкідливий
    no_pdfx_intent (warn рівня конфігурації) статус НЕ псує.
    """
    if any(i.level == "error" for i in report.issues):
        return "needs_human"
    return "ok"


def preflight_agent(
    spec: CanvasJSON,
    *,
    min_dpi: int = 300,
    min_bleed_mm: float = 3.0,
    max_iterations: int | None = None,
    renderer: Callable[[CanvasJSON], bytes] = render,
) -> AgentResult:
    """Гоняє issue -> fix -> re-check, поки дизайн не стане придатним до друку.

    `renderer` — сім для тестів (за замовчуванням справжній render() з gs);
    фінальний рендер робиться рівно один раз, у _finalize.
    """
    start = preflight(spec, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
    fixable0 = [i for i in start.issues if i.code in FIXABLE_CODES]
    if max_iterations is None:
        # N незалежних issue потребують N ітерацій (один фікс за ітерацію);
        # масштабуємо cap, щоб не давати хибний max_iterations_reached.
        max_iterations = max(8, 3 * len(fixable0))

    def _finalize(cur: CanvasJSON, iters: int, forced: AgentStatus | None) -> AgentResult:
        pdf = renderer(cur)  # рендер РІВНО один раз (B1)
        final = preflight(cur, pdf, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
        return AgentResult(
            spec=cur, pdf=pdf, report=final, iterations=iters,
            status=forced or _classify(final),
        )

    prev_sig: list[tuple[str, int | None]] | None = None
    for i in range(max_iterations):
        report = preflight(spec, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)  # без PDF (B1)
        fixable = [iss for iss in report.issues if iss.code in FIXABLE_CODES]

        if not fixable:  # spec-level чисто -> фінальний рендер + PDF-перевірки
            return _finalize(spec, i, forced=None)

        sig = sorted((iss.code, iss.element_index) for iss in fixable)
        if sig == prev_sig:  # фікс не зрушив набір issue -> конфлікт (A3)
            return _finalize(spec, i, forced="no_progress")
        prev_sig = sig

        # Один issue за ітерацію — найпростіша дисципліна Фази 1.
        issue = fixable[0]
        spec = FIXABLE_CODES[issue.code](
            spec, issue, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm
        )

    return _finalize(spec, max_iterations, forced="max_iterations_reached")
