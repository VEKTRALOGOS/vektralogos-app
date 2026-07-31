"""Preflight-агент на LangGraph StateGraph (Фаза 2а, спека §1).

Той самий контракт, що `server/preflight_agent.preflight_agent()` (Ф1, чистий
Python-цикл) — той самий allowlist фіксів, той самий фікспойнт-детектор, ті
самі статуси (`ok/needs_human/no_progress/max_iterations_reached`). Мета цього
модуля — НЕ новий функціонал, а переписати відомий, вже протестований домен на
граф, щоб навчитись LangGraph API (вузли, умовні ребра, checkpoints) там, де
поведінка вже перевірена (Ф1).

Граф (спека §1, 2а):

    [check_preflight] --(fixable є)--> [pick_and_apply_fix] --> [check_preflight]
           │
           └--(fixable немає)--> [finalize] --> END

    [pick_and_apply_fix]: якщо sig == prev_sig -> [finalize(no_progress)]
    [check_preflight] (за max_iterations) -> [finalize(max_iterations_reached)]

Checkpoint після кожного вузла — `MemorySaver` (in-memory, Фаза 2 рішення §3;
Postgres/Supabase — коли з'явиться реальна потреба в persistence між
процесами, не заздалегідь).
"""

from __future__ import annotations

from typing import Callable, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .preflight import PreflightReport, preflight
from .preflight_agent import FIXABLE_CODES, AgentResult, AgentStatus, _classify
from .render import render
from .schema import CanvasJSON

Signature = list[tuple[str, int | None]]


class GraphState(TypedDict):
    spec: CanvasJSON
    min_dpi: int
    min_bleed_mm: float
    max_iterations: int
    iterations: int  # скільки фіксів уже застосовано (той самий сенс, що i у Ф1-циклі)
    prev_signature: Signature | None
    report: PreflightReport | None  # останній spec-level preflight (без PDF)
    forced_status: AgentStatus | None  # встановлюється при no_progress/max_iterations
    result: AgentResult | None  # виставляється у finalize


def _signature(report: PreflightReport) -> Signature:
    fixable = [i for i in report.issues if i.code in FIXABLE_CODES]
    return sorted((iss.code, iss.element_index) for iss in fixable)


def _make_check_preflight(min_dpi: int, min_bleed_mm: float) -> Callable[[GraphState], dict]:
    def check_preflight(state: GraphState) -> dict:
        report = preflight(state["spec"], min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
        return {"report": report}

    return check_preflight


def _route_after_check(state: GraphState) -> str:
    report = state["report"]
    assert report is not None
    fixable = [i for i in report.issues if i.code in FIXABLE_CODES]
    if not fixable:
        return "finalize"
    if state["iterations"] >= state["max_iterations"]:
        return "finalize_max_iterations"
    return "apply_fix"


def _pick_and_apply_fix(state: GraphState) -> dict:
    report = state["report"]
    assert report is not None
    fixable = [i for i in report.issues if i.code in FIXABLE_CODES]
    sig = _signature(report)
    if sig == state["prev_signature"]:
        return {"forced_status": "no_progress"}

    # Один issue за ітерацію — та сама дисципліна, що в Ф1.
    issue = fixable[0]
    new_spec = FIXABLE_CODES[issue.code](
        state["spec"], issue,
        min_dpi=state["min_dpi"], min_bleed_mm=state["min_bleed_mm"],
    )
    return {
        "spec": new_spec,
        "prev_signature": sig,
        "iterations": state["iterations"] + 1,
    }


def _route_after_apply(state: GraphState) -> str:
    return "finalize" if state["forced_status"] is not None else "loop"


def _make_finalize(renderer: Callable[[CanvasJSON], bytes], min_dpi: int,
                    min_bleed_mm: float) -> Callable[[GraphState], dict]:
    def finalize(state: GraphState) -> dict:
        spec = state["spec"]
        pdf = renderer(spec)  # рендер РІВНО один раз (той самий принцип, що Ф1 B1)
        final = preflight(spec, pdf, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
        status = state["forced_status"] or _classify(final)
        result = AgentResult(
            spec=spec, pdf=pdf, report=final,
            iterations=state["iterations"], status=status,
        )
        return {"result": result}

    def finalize_max_iterations(state: GraphState) -> dict:
        # iterations уже == max_iterations тут (apply_fix інкрементує ДО повторної
        # перевірки check_preflight, яка й маршрутизує сюди) — лише проставляємо
        # forced_status для _classify, самé значення iterations не чіпаємо.
        return finalize({**state, "forced_status": "max_iterations_reached"})

    finalize_max_iterations.__name__ = "finalize_max_iterations"
    return finalize, finalize_max_iterations


def build_graph(
    *,
    min_dpi: int = 300,
    min_bleed_mm: float = 3.0,
    renderer: Callable[[CanvasJSON], bytes] = render,
    checkpointer: MemorySaver | None = None,
):
    """Компільований StateGraph. `checkpointer=None` -> новий MemorySaver."""
    finalize, finalize_max = _make_finalize(renderer, min_dpi, min_bleed_mm)

    g = StateGraph(GraphState)
    g.add_node("check_preflight", _make_check_preflight(min_dpi, min_bleed_mm))
    g.add_node("pick_and_apply_fix", _pick_and_apply_fix)
    g.add_node("finalize", finalize)
    g.add_node("finalize_max_iterations", finalize_max)

    g.add_edge("__start__", "check_preflight")
    g.add_conditional_edges(
        "check_preflight", _route_after_check,
        {"apply_fix": "pick_and_apply_fix", "finalize": "finalize",
         "finalize_max_iterations": "finalize_max_iterations"},
    )
    g.add_conditional_edges(
        "pick_and_apply_fix", _route_after_apply,
        {"loop": "check_preflight", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)
    g.add_edge("finalize_max_iterations", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def preflight_agent_graph(
    spec: CanvasJSON,
    *,
    min_dpi: int = 300,
    min_bleed_mm: float = 3.0,
    max_iterations: int | None = None,
    renderer: Callable[[CanvasJSON], bytes] = render,
    thread_id: str = "preflight",
) -> AgentResult:
    """Graph-версія `preflight_agent()` (Ф1) — той самий контракт входу/виходу.

    Обгортка над `build_graph()`: рахує дефолтний `max_iterations` так само,
    як Ф1-функція, ганяє граф з новим `MemorySaver` (checkpoint після
    кожного вузла) і повертає `AgentResult`.
    """
    if max_iterations is None:
        start = preflight(spec, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
        fixable0 = [i for i in start.issues if i.code in FIXABLE_CODES]
        max_iterations = max(8, 3 * len(fixable0))

    app = build_graph(
        min_dpi=min_dpi, min_bleed_mm=min_bleed_mm, renderer=renderer,
    )
    init: GraphState = {
        "spec": spec,
        "min_dpi": min_dpi,
        "min_bleed_mm": min_bleed_mm,
        "max_iterations": max_iterations,
        "iterations": 0,
        "prev_signature": None,
        "report": None,
        "forced_status": None,
        "result": None,
    }
    config = {"configurable": {"thread_id": thread_id}}
    out = app.invoke(init, config=config)
    result = out["result"]
    assert result is not None
    return result
