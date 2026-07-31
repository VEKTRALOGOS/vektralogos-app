"""Гібридний контракт Фази 0: prompt -> DesignBrief -> Canvas JSON.

Крок 1 (LLM): claude-opus-5 перетворює вільний текст на DesignBrief
             (server/brief.py) — семантика без координат.
Крок 2 (детермінований): шаблонизатор (server/templater.py) розкладає бриф
             у конкретний Canvas JSON із міліметровими позиціями.

Далі Canvas JSON напряму йде у render(). Секрети — лише з .env (§5 CLAUDE.md).
"""

from __future__ import annotations

from .brief import DesignBrief, prompt_to_brief
from .schema import CanvasJSON
from .templater import brief_to_canvas

# Іменовані розміри полотна (мм). Дефолт тестового прогону — A6 (листівка).
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "a6": (105.0, 148.0),
    "card": (90.0, 50.0),  # візитка
}
DEFAULT_PAPER = "a6"


def resolve_size(name: str) -> tuple[float, float]:
    key = name.lower()
    if key not in PAPER_SIZES:
        raise KeyError(f"Невідомий розмір '{name}'. Доступні: {sorted(PAPER_SIZES)}")
    return PAPER_SIZES[key]


def prompt_to_canvas(prompt: str, *, size: str = DEFAULT_PAPER) -> CanvasJSON:
    """prompt -> DesignBrief (LLM) -> Canvas JSON (шаблонизатор)."""
    brief = prompt_to_brief(prompt)
    return brief_from_prompt_to_canvas(brief, size=size)


def brief_from_prompt_to_canvas(brief: DesignBrief, *, size: str = DEFAULT_PAPER) -> CanvasJSON:
    """DesignBrief -> Canvas JSON для заданого розміру полотна."""
    width_mm, height_mm = resolve_size(size)
    return brief_to_canvas(brief, width_mm=width_mm, height_mm=height_mm)
