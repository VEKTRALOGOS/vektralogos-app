"""Мінімальні детерміновані evals на наявних фікстурах (Фаза 4b, спека §1 п.5).

Не LLM-judge: малий набір детермінованих перевірок якості наскрізного тракту
на тому, що вже в репо (приклад §6, фікстур відгуків, фікстур метрик). Дешево,
чесно, без мережі. LLM-judge — коли буде що масштабно оцінювати.

Кожен eval повертає `EvalResult(name, passed, detail)`. `run_evals()` збирає
всі; CLI `evals` друкує й повертає ненульовий код, якщо хоч один впав.
"""

from __future__ import annotations

from dataclasses import dataclass

from .director_graph import Metrics, validate_demand
from .preflight_agent import preflight_agent
from .product_graph import Preset, load_feedback, preset_to_sample_canvas
from .schema import CanvasJSON

_REVIEWS = "docs/research/fixtures/*.md"


@dataclass
class EvalResult:
    name: str
    passed: bool
    detail: str


def _clean_pdf(_spec: CanvasJSON) -> bytes:
    """Фейковий «чистий» PDF — evals детерміновані, без Ghostscript."""
    return b"%PDF-1.4\n%%EOF\n"


def eval_preflight_converges() -> EvalResult:
    """Приклад §6: bleed=1 + елемент за медіабоксом -> ok за <=3 ітерації."""
    spec = CanvasJSON.model_validate({
        "version": "1.0",
        "canvas": {"width_mm": 90, "height_mm": 50, "bleed_mm": 1},
        "fonts": [{"family": "Noto Sans", "file": "NotoSans-Regular.ttf"}],
        "elements": [{"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40,
                      "height_mm": 10, "fill": {"cmyk": [0, 0, 0, 1]}}],
    })
    r = preflight_agent(spec, renderer=_clean_pdf)
    ok = r.status == "ok" and r.iterations <= 3
    return EvalResult("preflight_converges", ok,
                      f"status={r.status}, iterations={r.iterations}")


def eval_preset_valid_and_printable() -> EvalResult:
    """Фіксований пресет -> прев'ю-canvas -> preflight ok (без LLM)."""
    preset = Preset(
        name="eval-preset", segment="POD", style="minimal",
        palette=["#111111", "#F5F5F5"], layout_hint="centered",
        sample_title="Ваш бренд", sample_name="Назва", rationale="eval",
    )
    canvas = preset_to_sample_canvas(preset)
    r = preflight_agent(canvas, renderer=_clean_pdf)
    return EvalResult("preset_valid_and_printable", r.status == "ok",
                      f"preflight={r.status}")


def eval_gate_signal_on_fixtures() -> EvalResult:
    """validate_demand: фікстур метрик + відгуки -> сигнал; обидва порожні -> ні."""
    feedback = load_feedback(_REVIEWS)
    has_reviews = validate_demand(Metrics(date="d", waitlist_signups=0), feedback)
    empty = validate_demand(Metrics(date="d", waitlist_signups=0), [])
    ok = has_reviews is True and empty is False
    return EvalResult("gate_signal_on_fixtures", ok,
                      f"reviews_signal={has_reviews}, empty_signal={empty}, n={len(feedback)}")


def run_evals() -> list[EvalResult]:
    """Усі evals. Детерміновані, без мережі/gs."""
    return [
        eval_preflight_converges(),
        eval_preset_valid_and_printable(),
        eval_gate_signal_on_fixtures(),
    ]
