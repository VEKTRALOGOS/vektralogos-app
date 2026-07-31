"""Vektralogos server — Python-only друкарський тракт (Фаза 0).

Гібридний контракт:
    prompt --LLM--> DesignBrief --templater--> Canvas JSON --render--> print.pdf

Публічний API:
    render(canvas_json) -> bytes            # Canvas JSON -> print.pdf (CMYK/ICC)
    prompt_to_brief(prompt) -> DesignBrief  # claude-opus-5, structured output
    brief_to_canvas(brief, ...) -> CanvasJSON
    prompt_to_canvas(prompt, size=...) -> CanvasJSON  # обидва кроки разом
"""

from .brief import DesignBrief, prompt_to_brief
from .prompt_to_canvas import prompt_to_canvas
from .render import render
from .schema import CanvasJSON
from .templater import brief_to_canvas

__all__ = [
    "CanvasJSON",
    "DesignBrief",
    "render",
    "prompt_to_brief",
    "brief_to_canvas",
    "prompt_to_canvas",
]
