"""Тести Product-агента (Фаза 2b, спека §6 acceptance).

LLM-вузол (`generate`) інжектимо фікс-пресетом; preflight-делегація і I/O —
реальні. Мережу не чіпаємо.
"""

from __future__ import annotations

import json

from server.preflight_agent import preflight_agent
from server.product_graph import (
    Preset,
    build_product_graph,
    load_feedback,
    parse_reviews,
    preset_to_sample_canvas,
    run_product_agent,
)
from server.schema import CanvasJSON

# --- сіми --------------------------------------------------------------------


def _fake_pdf(_spec: CanvasJSON) -> bytes:
    return b"%PDF-1.4\n% clean cmyk stub\n%%EOF\n"


def _fixed_preset(plan: str, feedback: list) -> Preset:
    return Preset(
        name="print-safe-bold",
        segment="POD-магазини з кириличним мерчем",
        style="minimal",
        palette=["#111111", "#F5F5F5"],
        layout_hint="centered",
        sample_title="Ваш бренд",
        sample_name="Назва товару",
        rationale="Адресує low-res-export і vector-missing: високий контраст, вектор.",
    )


_FIXTURE = "docs/research/fixtures/*.md"


# --- ingest / parse ----------------------------------------------------------


def test_parse_reviews_extracts_fields():
    md = (
        "## Review: Customily, 1★, 2026-02-14\n"
        "> Мильний PNG замість вектора.\n"
        "tags: low-res-export, vector-missing\n"
    )
    items = parse_reviews(md)
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "Customily" and it["stars"] == 1
    assert it["date"] == "2026-02-14"
    assert it["tags"] == ["low-res-export", "vector-missing"]
    assert "вектора" in it["text"]


def test_load_feedback_reads_fixture():
    items = load_feedback(_FIXTURE)
    assert len(items) >= 8  # спека §2: 8-10 записів
    assert any("low-res-export" in it["tags"] for it in items)


# --- матеріалізація пресету у прев'ю-canvas -----------------------------------


def test_preset_to_sample_canvas_is_valid_and_printable():
    canvas = preset_to_sample_canvas(_fixed_preset("", []))
    assert isinstance(canvas, CanvasJSON)
    # прев'ю чисте на рівні спеки -> preflight-агент дасть ok
    result = preflight_agent(canvas, renderer=_fake_pdf)
    assert result.status == "ok"


# --- повний прохід графа (acceptance 2b #1) ----------------------------------


def test_graph_reaches_prepare_diff_and_writes_preset(tmp_path):
    state = run_product_agent(
        reviews_glob=_FIXTURE, out_dir=tmp_path,
        generate=_fixed_preset, renderer=_fake_pdf,
    )
    assert state["status"] == "ok"
    assert state["plan"] and "Топ-скарги" in state["plan"]
    # валідний JSON-пресет на диску
    json_path = tmp_path / "print-safe-bold.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["name"] == "print-safe-bold"
    Preset.model_validate(data)  # валідний за схемою
    # текстовий опис поруч, з посиланням на джерела-відгуки
    desc = (tmp_path / "print-safe-bold.md").read_text(encoding="utf-8")
    assert "Відгуки-джерела" in desc and "Customily" in desc


def test_no_pr_is_created(tmp_path, monkeypatch):
    # автостворення PR заборонене (§4): гарантуємо, що gh не викликається
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("prepare_diff НЕ має викликати зовнішні процеси (gh)")

    monkeypatch.setattr(subprocess, "run", _boom)
    state = run_product_agent(
        reviews_glob=_FIXTURE, out_dir=tmp_path,
        generate=_fixed_preset, renderer=_fake_pdf,
    )
    assert state["status"] == "ok"


# --- порожній фікстур -> no_feedback (acceptance 2b #2) ----------------------


def test_empty_feedback_stops_no_feedback(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    (empty_dir / "nothing.md").write_text("# порожньо\n", encoding="utf-8")
    state = run_product_agent(
        reviews_glob="empty/*.md", root=tmp_path, out_dir=tmp_path / "out",
        generate=_fixed_preset, renderer=_fake_pdf,
    )
    assert state["status"] == "no_feedback"
    assert state["generated_preset"] is None  # нічого не «вигадали з нічого»
    assert not (tmp_path / "out").exists()  # diff не писався


# --- делегація у граф 2а (acceptance 2b #3) ----------------------------------


def test_run_preflight_delegates_to_phase2a_graph(tmp_path, monkeypatch):
    # мокаємо preflight У МОДУЛІ 2а -> якщо його викликано, отже run_preflight
    # реально прогнав граф 2а (preflight_agent_graph), а не копію логіки.
    import server.preflight_graph as pg

    calls = {"n": 0}
    real = pg.preflight

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(pg, "preflight", spy)
    state = run_product_agent(
        reviews_glob=_FIXTURE, out_dir=tmp_path,
        generate=_fixed_preset, renderer=_fake_pdf,
    )
    assert state["status"] == "ok"
    assert calls["n"] > 0  # граф 2а справді виконувався всередині вузла


def test_needs_human_when_preflight_not_ok(tmp_path):
    # preflight_runner, що завжди повертає needs_human -> граф іде у finalize
    from server.preflight_agent import AgentResult
    from server.preflight import PreflightReport

    def bad_runner(spec, **k):
        return AgentResult(spec=spec, pdf=b"%PDF", report=PreflightReport(ok=False, issues=[]),
                           iterations=0, status="needs_human")

    state = run_product_agent(
        reviews_glob=_FIXTURE, out_dir=tmp_path,
        generate=_fixed_preset, preflight_runner=bad_runner, renderer=_fake_pdf,
    )
    assert state["status"] == "needs_human"
    assert state["diff_path"] is None
    assert not any(tmp_path.iterdir())  # нічого не писалось
