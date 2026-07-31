"""Product Agent на LangGraph (Фаза 2b, спека §1 milestone 2b).

Граф поверх дисципліни 2а:

    [ingest_feedback] -> [plan] -> [generate_preset] -> [run_preflight (subgraph 2a)]
                                                                  │
                                (ok) ──────────────────────────────┼──> [prepare_diff] -> END
                                (needs_human/no_progress) ─────────┘──> [finalize_needs_human] -> END

    [ingest_feedback] (порожньо) -> [finalize_no_feedback] -> END

Свідомі рішення (спека, прийнято Антоном):
  * `generate_preset` — ЄДИНИЙ вузол з LLM у Ф2. Скоуп навмисно вузький:
    генерація ПРЕСЕТУ (набір стиль/палітра/розкладка під сегмент клієнтів),
    не довільна кодогенерація фіч (це Фаза 3, Director-Worker). §1.
  * `plan` — детермінований (агрегація тегів скарг), не LLM: тримаємо рівно
    один генеративний крок у фазі.
  * `run_preflight` делегує граф із 2а (`preflight_agent_graph`) як вузол —
    не переписує логіку вдруге. §1.
  * `prepare_diff` пише diff/патч на диск, БЕЗ `gh pr create`: автостворення
    зовнішнього артефакту — клас необоротних дій під approval-гейт Фази 4. §4.
  * `no_feedback` — чесний термінальний статус: без відгуків нічого не
    «вигадуємо з нічого» (той самий принцип, що non-fixable-ескалація Ф1 §5).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from .brief import DesignBrief, BriefTextElement, LayoutHint
from .preflight_agent import AgentResult
from .preflight_graph import preflight_agent_graph
from .render import render
from .schema import CanvasJSON
from .templater import brief_to_canvas

MODEL = "claude-opus-5"  # дефолт проєкту (DECISIONS.md)

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Джерело відгуків (спека §2): фікстур зараз, реальний ресёрч Gemini пізніше —
# той самий glob-підхід, що discover_docs у support_bot.py. Динамічний скан.
_DEFAULT_REVIEWS_GLOB = "docs/research/fixtures/*.md"
_DEFAULT_OUT_DIR = "presets"

_HEX_RGB = r"^#[0-9A-Fa-f]{6}$"


# --- моделі даних ------------------------------------------------------------


class FeedbackItem(TypedDict):
    source: str
    stars: int
    date: str
    text: str
    tags: list[str]


class Preset(BaseModel):
    """Згенерований пресет — вузький artifact Ф2 (не довільна фіча)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="kebab-case ідентифікатор, напр. print-safe-bold")
    segment: str = Field(description="цільовий сегмент клієнтів магазину")
    style: str = Field(description="напр. minimal, festive, formal")
    palette: list[str] = Field(min_length=1, max_length=3, description="1..3 hex #RRGGBB")
    layout_hint: LayoutHint
    sample_title: str = Field(description="приклад заголовка для прев'ю-рендеру")
    sample_name: str = Field(description="приклад імені/тексту для прев'ю-рендеру")
    rationale: str = Field(description="чому цей пресет — з посиланням на скарги")

    def model_post_init(self, __context) -> None:
        for c in self.palette:
            if not re.match(_HEX_RGB, c):
                raise ValueError(f"palette містить не-hex колір: {c!r}")


class ProductGraphState(TypedDict):
    feedback: list[FeedbackItem]
    plan: str | None
    generated_preset: dict | None
    preflight_result: AgentResult | None
    diff_path: str | None
    status: Literal["ok", "needs_human", "no_feedback"] | None


# --- ingest_feedback (спека §2) ----------------------------------------------

_REVIEW_HEADER = re.compile(
    r"^##\s+Review:\s*(?P<source>.+?),\s*(?P<stars>\d+)\s*★?,\s*(?P<date>\S+)\s*$",
    re.M,
)


def parse_reviews(text: str) -> list[FeedbackItem]:
    """Парсить блоки `## Review: <source>, <n>★, <date>` + цитата + `tags:`."""
    items: list[FeedbackItem] = []
    headers = list(_REVIEW_HEADER.finditer(text))
    for i, m in enumerate(headers):
        block = text[m.end() : (headers[i + 1].start() if i + 1 < len(headers) else len(text))]
        quote = ""
        tags: list[str] = []
        for line in block.splitlines():
            s = line.strip()
            if s.startswith(">") and not quote:
                quote = s.lstrip("> ").strip()
            elif s.lower().startswith("tags:"):
                tags = [t.strip() for t in s.split(":", 1)[1].split(",") if t.strip()]
        items.append(
            FeedbackItem(
                source=m.group("source").strip(),
                stars=int(m.group("stars")),
                date=m.group("date").strip(),
                text=quote,
                tags=tags,
            )
        )
    return items


def load_feedback(reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
                  root: Path = _REPO_ROOT) -> list[FeedbackItem]:
    """Динамічний скан .md за glob (той самий підхід, що support_bot §1)."""
    items: list[FeedbackItem] = []
    for path in sorted(root.glob(reviews_glob)):
        if path.is_file():
            items.extend(parse_reviews(path.read_text(encoding="utf-8")))
    return items


# --- deterministic plan ------------------------------------------------------


def _build_plan(feedback: list[FeedbackItem]) -> str:
    """Агрегує теги скарг у текстовий план (детерміновано, без LLM)."""
    counts: dict[str, int] = {}
    for item in feedback:
        for tag in item["tags"]:
            counts[tag] = counts.get(tag, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top_str = ", ".join(f"{tag} (×{n})" for tag, n in top[:5])
    return (
        f"Опрацьовано {len(feedback)} відгуків. Топ-скарги: {top_str}. "
        f"Ціль пресету: усунути ці болі друкованою коректністю "
        f"(вектор, CMYK, 300 DPI, вильоти, кирилиця в кривих)."
    )


# --- generate_preset (ЄДИНИЙ LLM-вузол) --------------------------------------

_GEN_SYSTEM = """Ти — дизайн-стратег друкованих пресетів для Shopify-персоналайзера.
На вхід — план і теги скарг клієнтів конкурентів. Згенеруй ОДИН пресет
(стиль/палітра/розкладка) під сегмент, що прямо адресує ці скарги.

Правила:
- palette — 1..3 hex #RRGGBB, з достатнім контрастом для друку.
- Ніяких товарних знаків/брендів третіх осіб у жодному полі.
- rationale — коротко, з посиланням на конкретні скарги (теги).
- Відповідай лише за схемою."""


def _default_generate(plan: str, feedback: list[FeedbackItem]) -> Preset:
    """claude-opus-5, structured output через beta.messages.parse (як brief.py)."""
    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY не заданий (додай у .env)")

    import anthropic

    all_tags = sorted({t for item in feedback for t in item["tags"]})
    user = f"План:\n{plan}\n\nТеги скарг: {', '.join(all_tags)}"
    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=_GEN_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=Preset,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Модель відмовила згенерувати пресет: {getattr(response, 'stop_details', None)}")
    preset = response.parsed_output
    if preset is None:
        raise RuntimeError(f"Не вдалося розібрати Preset (stop_reason={response.stop_reason})")
    return preset


def preset_to_sample_canvas(preset: Preset) -> CanvasJSON:
    """Матеріалізує пресет у прев'ю-CanvasJSON (для preflight) детерміновано."""
    brief = DesignBrief(
        style=preset.style,
        palette=preset.palette,
        layout_hint=preset.layout_hint,
        text_elements=[
            BriefTextElement(content=preset.sample_title, role="title"),
            BriefTextElement(content=preset.sample_name, role="name"),
        ],
    )
    return brief_to_canvas(brief, width_mm=90.0, height_mm=50.0)  # візитка


# --- граф --------------------------------------------------------------------


def build_product_graph(
    *,
    reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
    root: Path = _REPO_ROOT,
    out_dir: str | Path = _DEFAULT_OUT_DIR,
    generate: Callable[[str, list[FeedbackItem]], Preset] = _default_generate,
    preflight_runner: Callable[..., AgentResult] = preflight_agent_graph,
    renderer: Callable[[CanvasJSON], bytes] = render,
    checkpointer: MemorySaver | None = None,
):
    """Компільований Product-граф. Сіми (generate/preflight_runner/renderer/
    out_dir) інжектуються для тестів; дефолти — реальні."""
    out_path = Path(out_dir)

    def ingest_feedback(state: ProductGraphState) -> dict:
        return {"feedback": load_feedback(reviews_glob, root)}

    def route_after_ingest(state: ProductGraphState) -> str:
        return "plan" if state["feedback"] else "finalize_no_feedback"

    def plan_node(state: ProductGraphState) -> dict:
        return {"plan": _build_plan(state["feedback"])}

    def generate_preset(state: ProductGraphState) -> dict:
        preset = generate(state["plan"], state["feedback"])
        return {"generated_preset": preset.model_dump()}

    def run_preflight(state: ProductGraphState) -> dict:
        preset = Preset.model_validate(state["generated_preset"])
        sample = preset_to_sample_canvas(preset)
        result = preflight_runner(sample, renderer=renderer)  # делегує граф 2а
        return {"preflight_result": result}

    def route_after_preflight(state: ProductGraphState) -> str:
        return "prepare_diff" if state["preflight_result"].status == "ok" else "finalize_needs_human"

    def prepare_diff(state: ProductGraphState) -> dict:
        preset = state["generated_preset"]
        out_path.mkdir(parents=True, exist_ok=True)
        name = preset["name"]
        json_path = out_path / f"{name}.json"
        json_path.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")

        # Опис для майбутнього PR (заголовок + чому + джерела) — БЕЗ автостворення.
        sources = "\n".join(
            f"- {f['source']}, {f['stars']}★, {f['date']} ({', '.join(f['tags'])})"
            for f in state["feedback"]
        )
        desc = (
            f"# Пресет: {name}\n\n"
            f"**Сегмент:** {preset['segment']}\n\n"
            f"**Чому:** {preset['rationale']}\n\n"
            f"**План:** {state['plan']}\n\n"
            f"**Відгуки-джерела:**\n{sources}\n"
        )
        (out_path / f"{name}.md").write_text(desc, encoding="utf-8")
        return {"diff_path": str(json_path), "status": "ok"}

    def finalize_needs_human(state: ProductGraphState) -> dict:
        return {"status": "needs_human"}

    def finalize_no_feedback(state: ProductGraphState) -> dict:
        return {"status": "no_feedback"}

    g = StateGraph(ProductGraphState)
    g.add_node("ingest_feedback", ingest_feedback)
    g.add_node("plan", plan_node)
    g.add_node("generate_preset", generate_preset)
    g.add_node("run_preflight", run_preflight)
    g.add_node("prepare_diff", prepare_diff)
    g.add_node("finalize_needs_human", finalize_needs_human)
    g.add_node("finalize_no_feedback", finalize_no_feedback)

    g.add_edge("__start__", "ingest_feedback")
    g.add_conditional_edges(
        "ingest_feedback", route_after_ingest,
        {"plan": "plan", "finalize_no_feedback": "finalize_no_feedback"},
    )
    g.add_edge("plan", "generate_preset")
    g.add_edge("generate_preset", "run_preflight")
    g.add_conditional_edges(
        "run_preflight", route_after_preflight,
        {"prepare_diff": "prepare_diff", "finalize_needs_human": "finalize_needs_human"},
    )
    g.add_edge("prepare_diff", END)
    g.add_edge("finalize_needs_human", END)
    g.add_edge("finalize_no_feedback", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def run_product_agent(
    *,
    reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
    root: Path = _REPO_ROOT,
    out_dir: str | Path = _DEFAULT_OUT_DIR,
    generate: Callable[[str, list[FeedbackItem]], Preset] = _default_generate,
    preflight_runner: Callable[..., AgentResult] = preflight_agent_graph,
    renderer: Callable[[CanvasJSON], bytes] = render,
    thread_id: str = "product",
) -> ProductGraphState:
    """Ганяє Product-граф і повертає фінальний стан."""
    app = build_product_graph(
        reviews_glob=reviews_glob, root=root, out_dir=out_dir,
        generate=generate, preflight_runner=preflight_runner, renderer=renderer,
    )
    init: ProductGraphState = {
        "feedback": [], "plan": None, "generated_preset": None,
        "preflight_result": None, "diff_path": None, "status": None,
    }
    config = {"configurable": {"thread_id": thread_id}}
    return app.invoke(init, config=config)
