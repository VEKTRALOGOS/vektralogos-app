"""Vektralogos server — Python-only друкарський тракт (Фаза 0).

Публічний API:
    render(canvas_json) -> bytes   # Canvas JSON -> print.pdf (вектор, CMYK/ICC)
    prompt_to_canvas(prompt) -> CanvasJSON   # claude-opus-5, structured output
"""

from .schema import CanvasJSON
from .render import render

__all__ = ["CanvasJSON", "render"]
