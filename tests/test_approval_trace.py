"""Тести approval-гейта + трейсу (Фаза 4a, спека §2.1–§3, §4 acceptance).

Гейт (`interrupt_before`) + governance тестуємо офлайн: `pr_creator` мокаємо
(жодного реального `gh pr create`), рішення людини — через `decide`-сім.
"""

from __future__ import annotations

import json

from server.preflight_agent import AgentResult
from server.preflight import PreflightReport
from server.product_graph import Preset, run_product_with_approval
from server.schema import CanvasJSON

_REVIEWS = "docs/research/fixtures/*.md"


def _fixed_preset(plan, feedback) -> Preset:
    return Preset(
        name="print-safe-bold", segment="POD", style="minimal",
        palette=["#111111", "#F5F5F5"], layout_hint="centered",
        sample_title="Ваш бренд", sample_name="Назва", rationale="проти low-res.",
    )


def _fake_pdf(_spec: CanvasJSON) -> bytes:
    return b"%PDF-1.4\n%%EOF\n"


def _ok_preflight(spec, **kwargs) -> AgentResult:
    return AgentResult(spec=spec, pdf=b"%PDF", report=PreflightReport(ok=True, issues=[]),
                       iterations=0, status="ok")


def _run(tmp_path, decide, pr_creator, thread_id="t-appr"):
    return run_product_with_approval(
        reviews_glob=_REVIEWS, out_dir=tmp_path / "presets",
        traces_dir=tmp_path / "traces", thread_id=thread_id,
        decide=decide, generate=_fixed_preset, preflight_runner=_ok_preflight,
        renderer=_fake_pdf, pr_creator=pr_creator, approver="tester",
    )


# --- approve -> реальна дія (мок) --------------------------------------------


def test_approve_opens_pr_once(tmp_path):
    calls = {"n": 0}

    def pr_creator(preset, diff_path, desc_path) -> str:
        calls["n"] += 1
        return "https://github.com/x/y/pull/42"

    state = _run(tmp_path, decide=lambda d, s: "approve", pr_creator=pr_creator)
    assert state["status"] == "applied"
    assert state["pr_url"] == "https://github.com/x/y/pull/42"
    assert calls["n"] == 1  # відкрито рівно один раз, лише після approve


# --- reject -> дії немає, diff лишається (§2.1) ------------------------------


def test_reject_does_not_open_pr_and_keeps_diff(tmp_path):
    calls = {"n": 0}

    def pr_creator(*a, **k) -> str:
        calls["n"] += 1
        return "nope"

    state = _run(tmp_path, decide=lambda d, s: "reject", pr_creator=pr_creator)
    assert state["status"] == "rejected"
    assert calls["n"] == 0  # gh НЕ викликано
    # diff-файл лишається на диску (§2.1)
    assert (tmp_path / "presets" / "print-safe-bold.json").exists()


# --- «будь-що крім approve» = reject (явне слово, §2.1) ----------------------


def test_non_approve_word_is_treated_as_reject(tmp_path):
    def pr_creator(*a, **k) -> str:
        raise AssertionError("не має викликатись без точного 'approve'")

    # decide повертає 'y' -> run_product_with_approval сам не мапить; мапінг слова
    # робить CLI. Тут перевіряємо, що лише точне 'approve' відкриває PR:
    state = _run(tmp_path, decide=lambda d, s: "y", pr_creator=pr_creator)
    assert state["status"] == "rejected"


# --- трейс: аудит approve (§2.1, §3) -----------------------------------------


def test_trace_records_full_path_and_audit(tmp_path):
    _run(tmp_path, decide=lambda d, s: "approve",
         pr_creator=lambda *a, **k: "https://github.com/x/y/pull/7",
         thread_id="t-trace")
    trace = json.loads((tmp_path / "traces" / "t-trace.json").read_text(encoding="utf-8"))
    assert trace["thread_id"] == "t-trace" and trace["graph"] == "product"
    nodes = [s["node"] for s in trace["steps"]]
    # усі пройдені вузли у трейсі
    for n in ("ingest_feedback", "plan", "generate_preset", "run_preflight",
              "prepare_diff", "approval_gate", "open_pr"):
        assert n in nodes, f"вузол {n} відсутній у трейсі"
    # аудит на approve-кроці: approver, ts, diff_hash
    approved = next(s for s in trace["steps"]
                    if s["node"] == "approval_gate" and s["status"] == "approved")
    assert approved["approver"] == "tester"
    assert approved["ts"] and approved["diff_hash"]
    # waiting-крок зафіксовано до рішення
    assert any(s["node"] == "approval_gate" and s["status"] == "waiting"
               for s in trace["steps"])
    # open_pr зафіксував pr_url
    applied = next(s for s in trace["steps"] if s["node"] == "open_pr")
    assert applied["status"] == "applied" and applied["pr_url"].endswith("/7")


def test_trace_reject_has_no_open_pr(tmp_path):
    _run(tmp_path, decide=lambda d, s: "reject",
         pr_creator=lambda *a, **k: "x", thread_id="t-rej")
    trace = json.loads((tmp_path / "traces" / "t-rej.json").read_text(encoding="utf-8"))
    nodes = [(s["node"], s["status"]) for s in trace["steps"]]
    assert ("finalize_rejected", "rejected") in nodes
    assert not any(n == "open_pr" for n, _ in nodes)


# --- гейт справді зупиняє (interrupt_before), diff уже на диску до рішення ----


def test_gate_pauses_before_action_diff_exists_pre_decision(tmp_path):
    seen = {}

    def decide(diff_text, desc_text) -> str:
        # у момент рішення diff уже згенерований і показаний людині
        seen["diff"] = diff_text
        seen["desc"] = desc_text
        return "reject"

    _run(tmp_path, decide=decide, pr_creator=lambda *a, **k: "x")
    assert "print-safe-bold" in seen["diff"]  # diff показано ДО дії
    assert "Сегмент" in seen["desc"]


# --- Ф2b без гейта не зачеплено (регрес) -------------------------------------


def test_phase2b_without_approval_unchanged(tmp_path):
    from server.product_graph import run_product_agent

    state = run_product_agent(
        reviews_glob=_REVIEWS, out_dir=tmp_path / "p",
        generate=_fixed_preset, preflight_runner=_ok_preflight, renderer=_fake_pdf,
    )
    assert state["status"] == "ok"  # старий контракт Ф2b
