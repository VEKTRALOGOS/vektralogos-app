"""Director / Orchestrator на LangGraph (Фаза 3, спека §1–§4).

Координатор делегує воркерам через subgraphs-як-вузли (той самий патерн, що
2b викликає граф 2а) і спільний `DirectorState`. Перший вузол — детермінований
гейт `validate_demand` (STRATEGY §4 «паттерн гейта перед постройкой освой
сразу»); approval-гейти на необоротне лишаються Фазі 4.

Граф (канонічна схема — вхідний документ Ф3, §2):

    [validate_demand] --(сигнал є)--> fan-out воркери --> [collect_results] --> [finalize] -> END
           │
           └--(сигналу нема)--> [finalize_no_signal] -> END

Milestone:
  * 3a — Director + Product-воркер (subgraph 2b). Routing спроєктований під
    fan-out одразу (список `enabled_workers`), щоб 3b лише додавав воркери,
    не переробляв `route_to_workers` (спека, «наступний крок»).
  * 3b — додати Marketing + Sales/Support воркери, що виконуються ПАРАЛЕЛЬНО
    (fan-out/fan-in, спека §2.1).

Термінальні статуси: `ok` / `no_signal` (гейт не пропустив) / `needs_human`
(воркер повернув needs_human).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Callable, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict

from .product_graph import FeedbackItem, load_feedback, run_product_agent
from .reliability import account, retry
from .support_bot import ask

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_METRICS_PATH = "docs/research/fixtures/metrics.json"
_DEFAULT_REVIEWS_GLOB = "docs/research/fixtures/*.md"

# Ім'я воркер-ключа -> ім'я вузла графа. 3a реєструє лише product.
_WORKER_NODE = {
    "product": "product_worker",
    "marketing": "marketing_worker",
    "sales_support": "sales_support_worker",
}


class Metrics(BaseModel):
    """Метрики продукту (фікстур зараз, реальне джерело — пізніше). Спека §3."""

    model_config = ConfigDict(extra="forbid")
    date: str
    installs: int = 0
    mrr_usd: float = 0.0
    rating: float | None = None
    churn_pct: float | None = None
    waitlist_signups: int = 0


def _merge_results(a: dict, b: dict) -> dict:
    """Reducer для паралельних записів воркерів у спільний worker_results."""
    merged = dict(a)
    merged.update(b)
    return merged


class DirectorState(TypedDict):
    metrics: dict | None
    feedback: list[FeedbackItem]
    signal: bool | None
    worker_results: Annotated[dict, _merge_results]
    status: Literal["ok", "no_signal", "needs_human"] | None


# --- гейт (детермінований, без LLM — консистентно з Ф1/2a) --------------------


def load_metrics(metrics_path: str = _DEFAULT_METRICS_PATH,
                 root: Path = _REPO_ROOT) -> Metrics:
    data = json.loads((root / metrics_path).read_text(encoding="utf-8"))
    return Metrics.model_validate(data)


def validate_demand(metrics: Metrics, feedback: list[FeedbackItem]) -> bool:
    """Сигнал = є вейтлист-заявки АБО релевантні конкурентні відгуки (спека §3).

    installs/mrr/rating/churn у фікстурі для майбутнього (Ф4, коли installs>0
    стане реальним порогом), але в логіці гейта Ф3 не беруть участі — чесніше,
    ніж удавати поріг на нулях до лістингу.
    """
    return metrics.waitlist_signups > 0 or len(feedback) > 0


# --- воркери-скелети 3b (тонкі, повна автономія — Ф5) ------------------------

MODEL = "claude-opus-5"  # дефолт проєкту (DECISIONS.md)

_MARKETING_SYSTEM = """Ти — маркетолог Shopify-застосунку Vektralogos (друкарський
вектор: CMYK, 300 DPI, кирилиця в кривих). На вхід — теги скарг клієнтів
конкурентів. Напиши ОДИН короткий чернетковий фрагмент для лістингу App Store
(2-3 речення), що прямо б'є в ці болі. Без брендів третіх осіб. Тільки текст."""


def _default_marketing(feedback: list[FeedbackItem], metrics: dict | None) -> dict:
    """Тонкий LLM-вузол: чернетка маркетинг-контенту (claude-opus-5, plain text)."""
    import os

    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY не заданий (додай у .env)")

    import anthropic

    tags = sorted({t for item in feedback for t in item["tags"]})
    client = anthropic.Anthropic()

    def _call():
        r = client.messages.create(
            model=MODEL, max_tokens=500, system=_MARKETING_SYSTEM,
            messages=[{"role": "user", "content": f"Теги скарг: {', '.join(tags)}"}],
        )
        account(r)  # per-run token budget (Ф4b)
        return r

    transient = (anthropic.APIConnectionError, anthropic.APITimeoutError,
                 anthropic.RateLimitError, anthropic.InternalServerError)
    resp = retry(_call, attempts=3, exceptions=transient)
    draft = "".join(b.text for b in resp.content if b.type == "text").strip()
    return {"status": "ok", "draft": draft}


# Питання-заглушка для онбордингу: Sales/Support відповідає по доці (retrieval Ф1).
_SUPPORT_QUESTION = "Як Vektralogos гарантує друкарську якість (вектор, CMYK, 300 DPI, кирилиця)?"


def _default_sales_support(feedback: list[FeedbackItem]) -> dict:
    """Переюз retrieval Ф1 support-бота (ask) — доко-обґрунтована відповідь."""
    answer = ask(_SUPPORT_QUESTION)
    return {"status": "ok", "answer": answer, "question": _SUPPORT_QUESTION}


# --- граф --------------------------------------------------------------------


def build_director_graph(
    *,
    metrics_path: str = _DEFAULT_METRICS_PATH,
    reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
    root: Path = _REPO_ROOT,
    enabled_workers: tuple[str, ...] = ("product", "marketing", "sales_support"),
    product_runner: Callable[..., dict] | None = None,
    marketing_runner: Callable[..., dict] | None = None,
    sales_support_runner: Callable[..., dict] | None = None,
    checkpointer: MemorySaver | None = None,
):
    """Компільований Director-граф. Сіми (runners/шляхи) інжектуються для тестів.

    `enabled_workers` — які воркери у fan-out (виконуються ПАРАЛЕЛЬНО, §2.1).
    Дефолт — усі три (повний Ф3). Runners=None резолвляться ліниво до модульних
    дефолтів (щоб лишатись патчабельними у тестах).
    """
    worker_nodes = [_WORKER_NODE[w] for w in enabled_workers]
    _product_runner = product_runner if product_runner is not None else run_product_agent
    _marketing_runner = marketing_runner if marketing_runner is not None else _default_marketing
    _sales_support_runner = (
        sales_support_runner if sales_support_runner is not None else _default_sales_support
    )

    def validate_demand_node(state: DirectorState) -> dict:
        metrics = load_metrics(metrics_path, root)
        feedback = load_feedback(reviews_glob, root)
        return {
            "metrics": metrics.model_dump(),
            "feedback": feedback,
            "signal": validate_demand(metrics, feedback),
        }

    def route_to_workers(state: DirectorState):
        # Сигнал є -> fan-out на всі увімкнені воркери (паралельно, §2.1).
        # Немає -> чесна зупинка.
        return worker_nodes if state["signal"] else "finalize_no_signal"

    def product_worker(state: DirectorState) -> dict:
        result = _product_runner(reviews_glob=reviews_glob, root=root)
        return {"worker_results": {"product": {
            "status": result["status"], "diff_path": result.get("diff_path"),
        }}}

    def marketing_worker(state: DirectorState) -> dict:
        return {"worker_results": {"marketing": _marketing_runner(
            state["feedback"], state["metrics"])}}

    def sales_support_worker(state: DirectorState) -> dict:
        return {"worker_results": {"sales_support": _sales_support_runner(state["feedback"])}}

    def collect_results(state: DirectorState) -> dict:
        # Fan-in: reducer уже злив воркерів. Виводимо агрегований статус.
        statuses = [w.get("status") for w in state["worker_results"].values()]
        status = "needs_human" if "needs_human" in statuses else "ok"
        return {"status": status}

    def finalize(state: DirectorState) -> dict:
        return {}

    def finalize_no_signal(state: DirectorState) -> dict:
        return {"status": "no_signal"}

    _worker_fns = {
        "product_worker": product_worker,
        "marketing_worker": marketing_worker,
        "sales_support_worker": sales_support_worker,
    }

    g = StateGraph(DirectorState)
    g.add_node("validate_demand", validate_demand_node)
    for n in worker_nodes:  # реєструємо лише увімкнені воркери
        g.add_node(n, _worker_fns[n])
    g.add_node("collect_results", collect_results)
    g.add_node("finalize", finalize)
    g.add_node("finalize_no_signal", finalize_no_signal)

    g.add_edge("__start__", "validate_demand")
    path_map = {n: n for n in worker_nodes}
    path_map["finalize_no_signal"] = "finalize_no_signal"
    g.add_conditional_edges("validate_demand", route_to_workers, path_map)
    for n in worker_nodes:
        g.add_edge(n, "collect_results")
    g.add_edge("collect_results", "finalize")
    g.add_edge("finalize", END)
    g.add_edge("finalize_no_signal", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def run_director(
    *,
    metrics_path: str = _DEFAULT_METRICS_PATH,
    reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
    root: Path = _REPO_ROOT,
    enabled_workers: tuple[str, ...] = ("product", "marketing", "sales_support"),
    product_runner: Callable[..., dict] | None = None,
    marketing_runner: Callable[..., dict] | None = None,
    sales_support_runner: Callable[..., dict] | None = None,
    thread_id: str = "director",
) -> DirectorState:
    """Ганяє Director-граф і повертає фінальний стан."""
    app = build_director_graph(
        metrics_path=metrics_path, reviews_glob=reviews_glob, root=root,
        enabled_workers=enabled_workers, product_runner=product_runner,
        marketing_runner=marketing_runner, sales_support_runner=sales_support_runner,
    )
    init: DirectorState = {
        "metrics": None, "feedback": [], "signal": None,
        "worker_results": {}, "status": None,
    }
    config = {"configurable": {"thread_id": thread_id}}
    return app.invoke(init, config=config)
