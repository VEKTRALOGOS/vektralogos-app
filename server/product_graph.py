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
import hashlib
import re
from datetime import datetime, timezone
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
    # Лише серіалізовний зріз результату preflight (не весь AgentResult з
    # CanvasJSON) — щоб checkpoint MemorySaver не тягнув багаті типи (Ф4a).
    preflight_result: dict | None
    diff_path: str | None
    # Ф4 approval-хвіст (лишається None у режимі Ф2b без гейта):
    approval: str | None  # "approve" | "reject", проставляється людиною через update_state
    pr_url: str | None  # з open_pr після approve
    status: Literal["ok", "needs_human", "no_feedback", "applied", "rejected"] | None


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


# --- трейс + governance (Ф4a) ------------------------------------------------


def _trace_step(trace_path: str | Path | None, node: str, status: str, **extra) -> None:
    """Дописує крок у JSON-трейс прогону (traces/<thread_id>.json). No-op, якщо
    трейс не увімкнено (режим Ф2b без гейта)."""
    if trace_path is None:
        return
    p = Path(trace_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"steps": []}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    step = {"node": node, "status": status, "ts": datetime.now(timezone.utc).isoformat()}
    step.update(extra)
    data.setdefault("steps", []).append(step)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_open_pr(preset: dict, diff_path: str, desc_path: str | Path) -> str:
    """РЕАЛЬНЕ `gh pr create` ПІСЛЯ approve (спека §0, §2.1). Створює гілку,
    комітить пресет (force-add, бо presets/ у .gitignore), пушить, відкриває PR.

    Живий шлях лише через CLI `propose-and-open-pr` після явного approve —
    у тестах інжектимо fake pr_creator, тут нічого не викликається.
    """
    import subprocess

    name = preset["name"]
    branch = f"preset/{name}"

    def sh(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)

    sh(["git", "checkout", "-b", branch])
    sh(["git", "add", "-f", str(diff_path), str(desc_path)])
    sh(["git", "commit", "-m", f"preset: {name} (Product-агент, approved)"])
    sh(["git", "push", "-u", "origin", branch])
    result = sh(["gh", "pr", "create", "--fill", "--base", "main", "--head", branch])
    return result.stdout.strip()


# --- граф --------------------------------------------------------------------


def build_product_graph(
    *,
    reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
    root: Path = _REPO_ROOT,
    out_dir: str | Path = _DEFAULT_OUT_DIR,
    generate: Callable[[str, list[FeedbackItem]], Preset] = _default_generate,
    preflight_runner: Callable[..., AgentResult] = preflight_agent_graph,
    renderer: Callable[[CanvasJSON], bytes] = render,
    with_approval: bool = False,
    pr_creator: Callable[..., str] | None = None,
    trace_path: str | Path | None = None,
    approver: str | None = None,
    checkpointer: MemorySaver | None = None,
):
    """Компільований Product-граф. Сіми (generate/preflight_runner/renderer/
    out_dir) інжектуються для тестів; дефолти — реальні.

    `with_approval=True` (Ф4a) додає хвіст `prepare_diff → approval_gate
    (interrupt_before) → open_pr | finalize_rejected` з трейсом і governance.
    За замовчуванням (False) — поведінка Ф2b без змін.
    """
    import os

    out_path = Path(out_dir)
    _pr_creator = pr_creator if pr_creator is not None else _default_open_pr

    def ingest_feedback(state: ProductGraphState) -> dict:
        fb = load_feedback(reviews_glob, root)
        _trace_step(trace_path, "ingest_feedback", "ok")
        return {"feedback": fb}

    def route_after_ingest(state: ProductGraphState) -> str:
        return "plan" if state["feedback"] else "finalize_no_feedback"

    def plan_node(state: ProductGraphState) -> dict:
        _trace_step(trace_path, "plan", "ok")
        return {"plan": _build_plan(state["feedback"])}

    def generate_preset(state: ProductGraphState) -> dict:
        preset = generate(state["plan"], state["feedback"])
        _trace_step(trace_path, "generate_preset", "ok")
        return {"generated_preset": preset.model_dump()}

    def run_preflight(state: ProductGraphState) -> dict:
        preset = Preset.model_validate(state["generated_preset"])
        sample = preset_to_sample_canvas(preset)
        result = preflight_runner(sample, renderer=renderer)  # делегує граф 2а
        _trace_step(trace_path, "run_preflight", result.status)
        return {"preflight_result": {"status": result.status, "iterations": result.iterations}}

    def route_after_preflight(state: ProductGraphState) -> str:
        return "prepare_diff" if state["preflight_result"]["status"] == "ok" else "finalize_needs_human"

    def prepare_diff(state: ProductGraphState) -> dict:
        preset = state["generated_preset"]
        out_path.mkdir(parents=True, exist_ok=True)
        name = preset["name"]
        json_path = out_path / f"{name}.json"
        json_path.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")

        # Опис для PR (заголовок + чому + джерела).
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
        _trace_step(trace_path, "prepare_diff", "ok")
        # У режимі гейта фінальний статус ставлять open_pr/finalize_rejected.
        return {"diff_path": str(json_path)} if with_approval else {
            "diff_path": str(json_path), "status": "ok"
        }

    def approval_gate(state: ProductGraphState) -> dict:
        # interrupt_before зупиняє граф ДО цього вузла; людина проставляє
        # state["approval"] через update_state, далі граф відновлюється сюди.
        decision = state.get("approval")
        diff_path_v = state.get("diff_path")
        diff_hash = None
        if diff_path_v:
            diff_hash = hashlib.sha256(Path(diff_path_v).read_bytes()).hexdigest()[:12]
        who = approver or os.environ.get("USER") or "unknown"
        status = "approved" if decision == "approve" else "rejected"
        _trace_step(trace_path, "approval_gate", status, approver=who, diff_hash=diff_hash)
        return {}

    def route_after_gate(state: ProductGraphState) -> str:
        return "open_pr" if state.get("approval") == "approve" else "finalize_rejected"

    def open_pr(state: ProductGraphState) -> dict:
        preset = state["generated_preset"]
        url = _pr_creator(preset, state["diff_path"], out_path / f"{preset['name']}.md")
        _trace_step(trace_path, "open_pr", "applied", pr_url=url)
        return {"pr_url": url, "status": "applied"}

    def finalize_rejected(state: ProductGraphState) -> dict:
        # diff-файл НЕ видаляємо (§2.1) — лишається для ручного review/PR.
        _trace_step(trace_path, "finalize_rejected", "rejected")
        return {"status": "rejected"}

    def finalize_needs_human(state: ProductGraphState) -> dict:
        _trace_step(trace_path, "finalize_needs_human", "needs_human")
        return {"status": "needs_human"}

    def finalize_no_feedback(state: ProductGraphState) -> dict:
        _trace_step(trace_path, "finalize_no_feedback", "no_feedback")
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
    g.add_edge("finalize_needs_human", END)
    g.add_edge("finalize_no_feedback", END)

    if with_approval:
        g.add_node("approval_gate", approval_gate)
        g.add_node("open_pr", open_pr)
        g.add_node("finalize_rejected", finalize_rejected)
        g.add_edge("prepare_diff", "approval_gate")
        g.add_conditional_edges(
            "approval_gate", route_after_gate,
            {"open_pr": "open_pr", "finalize_rejected": "finalize_rejected"},
        )
        g.add_edge("open_pr", END)
        g.add_edge("finalize_rejected", END)
        return g.compile(
            checkpointer=checkpointer or MemorySaver(),
            interrupt_before=["approval_gate"],  # human-in-the-loop (спека §2.2)
        )

    g.add_edge("prepare_diff", END)
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
        "preflight_result": None, "diff_path": None,
        "approval": None, "pr_url": None, "status": None,
    }
    config = {"configurable": {"thread_id": thread_id}}
    return app.invoke(init, config=config)


def run_product_with_approval(
    *,
    reviews_glob: str = _DEFAULT_REVIEWS_GLOB,
    root: Path = _REPO_ROOT,
    out_dir: str | Path = _DEFAULT_OUT_DIR,
    decide: Callable[[str, str], str],
    generate: Callable[[str, list[FeedbackItem]], Preset] | None = None,
    preflight_runner: Callable[..., AgentResult] | None = None,
    renderer: Callable[[CanvasJSON], bytes] | None = None,
    pr_creator: Callable[..., str] | None = None,
    approver: str | None = None,
    thread_id: str = "product-approval",
    traces_dir: str | Path = "traces",
) -> ProductGraphState:
    """Ганяє Product-граф з approval-гейтом (Ф4a), СИНХРОННО в одному процесі.

    `decide(diff_text, desc_text) -> "approve"|"reject"` — рішення людини (CLI
    показує diff і питає слово `approve`; тести інжектять фікс-рішення). Реальний
    `gh pr create` — лише після `approve`. На `reject` diff-файл лишається (§2.1).
    """
    trace_path = Path(traces_dir) / f"{thread_id}.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps({"thread_id": thread_id, "graph": "product", "steps": []},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    kwargs: dict = dict(
        reviews_glob=reviews_glob, root=root, out_dir=out_dir,
        with_approval=True, trace_path=trace_path, approver=approver,
    )
    if generate is not None:
        kwargs["generate"] = generate
    if preflight_runner is not None:
        kwargs["preflight_runner"] = preflight_runner
    if renderer is not None:
        kwargs["renderer"] = renderer
    if pr_creator is not None:
        kwargs["pr_creator"] = pr_creator
    app = build_product_graph(**kwargs)

    config = {"configurable": {"thread_id": thread_id}}
    init: ProductGraphState = {
        "feedback": [], "plan": None, "generated_preset": None,
        "preflight_result": None, "diff_path": None,
        "approval": None, "pr_url": None, "status": None,
    }
    app.invoke(init, config=config)

    snap = app.get_state(config)
    if snap.next and "approval_gate" in snap.next:
        # Досягли гейта -> граф на паузі (interrupt_before). Питаємо людину.
        _trace_step(trace_path, "approval_gate", "waiting")
        preset = snap.values["generated_preset"]
        diff_text = Path(snap.values["diff_path"]).read_text(encoding="utf-8")
        desc_text = (Path(out_dir) / f"{preset['name']}.md").read_text(encoding="utf-8")
        decision = decide(diff_text, desc_text)
        app.update_state(config, {"approval": decision})
        app.invoke(None, config=config)  # відновлюємо в тому ж процесі

    return app.get_state(config).values
