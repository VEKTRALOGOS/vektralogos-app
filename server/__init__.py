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
from .preflight import PreflightReport, preflight
from .preflight_agent import AgentResult, preflight_agent
from .director_graph import run_director
from .evals import run_evals
from .managed_support import (
    build_agent_config,
    compare_answers,
    estimate_cost,
    run_managed_support,
)
from .preflight_graph import preflight_agent_graph
from .reliability import BudgetExceeded, TokenBudget, budget_scope, retry
from .product_graph import Preset, run_product_agent, run_product_with_approval
from .prompt_to_canvas import prompt_to_canvas
from .render import render
from .schema import CanvasJSON
from .support_bot import Retriever, ask, load_chunks
from .templater import brief_to_canvas

__all__ = [
    "CanvasJSON",
    "DesignBrief",
    "render",
    "prompt_to_brief",
    "brief_to_canvas",
    "prompt_to_canvas",
    "preflight",
    "PreflightReport",
    "preflight_agent",
    "AgentResult",
    "preflight_agent_graph",
    "run_product_agent",
    "run_product_with_approval",
    "Preset",
    "run_director",
    "run_evals",
    "build_agent_config",
    "compare_answers",
    "estimate_cost",
    "run_managed_support",
    "retry",
    "TokenBudget",
    "BudgetExceeded",
    "budget_scope",
    "ask",
    "Retriever",
    "load_chunks",
]
