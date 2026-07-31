"""Тести Support doc-QA на CMA (Фаза 5a) — усе офлайн, БЕЗ live-прогонів.

Live CMA-сесії платні (спека §2) і запускаються лише після явного «go» Антона —
тут перевіряємо конфіг агента, оцінку вартості, порівняльну логіку, teardown і
що live-шлях НАДІЙНО заблокований без allow_live.
"""

from __future__ import annotations

import pytest

from server.managed_support import (
    AGENT_NAME,
    CONTROL_QUESTIONS,
    MODEL,
    build_agent_config,
    compare_answers,
    estimate_cost,
    run_managed_support,
    session_resources,
    teardown,
)


# --- конфіг агента -----------------------------------------------------------


def test_build_agent_config_shape():
    cfg = build_agent_config()
    assert cfg["name"] == AGENT_NAME
    assert cfg["model"] == MODEL  # claude-opus-5
    assert cfg["tools"] == [{"type": "agent_toolset_20260401"}]
    assert "grep" in cfg["system"] and "джерело" in cfg["system"]  # retrieval-вказівка


def test_session_resources_mounts_repo_readonly_branch():
    res = session_resources(token="ghp_x")
    assert len(res) == 1
    r = res[0]
    assert r["type"] == "github_repository"
    assert r["authorization_token"] == "ghp_x"
    assert r["checkout"] == {"type": "branch", "name": "main"}


# --- оцінка вартості (§2.2 — конкретні числа) --------------------------------


def test_estimate_cost_is_positive_and_transparent():
    est = estimate_cost()
    assert est.n_questions == len(CONTROL_QUESTIONS)
    assert est.total > 0
    # сума = сесія + токени
    assert abs(est.total - (est.session_cost + est.token_cost)) < 1e-9
    s = est.summary()
    assert "РАЗОМ" in s and "$" in s


def test_estimate_cost_scales_with_questions():
    small = estimate_cost(n_questions=1)
    big = estimate_cost(n_questions=10)
    assert big.total > small.total


# --- порівняння CMA vs Ф1 ----------------------------------------------------


def test_compare_answers_pairs_cma_and_phase1():
    def fake_cma(q: str) -> str:
        return f"CMA:{q[:10]}"

    def fake_phase1(q: str) -> str:
        return f"Ф1:{q[:10]}"

    rows = compare_answers(fake_cma, questions=["питання одне", "питання два"],
                           phase1_answer=fake_phase1)
    assert len(rows) == 2
    assert rows[0].cma_answer.startswith("CMA:")
    assert rows[0].phase1_answer.startswith("Ф1:")
    assert rows[0].question == "питання одне"


def test_compare_uses_all_control_questions_by_default():
    rows = compare_answers(lambda q: "x", phase1_answer=lambda q: "y")
    assert [r.question for r in rows] == CONTROL_QUESTIONS


# --- teardown (§2.3) ---------------------------------------------------------


def test_teardown_archives_agent():
    calls = {}

    class _Agents:
        def archive(self, agent_id):
            calls["archived"] = agent_id

    class _Beta:
        agents = _Agents()

    class _Client:
        beta = _Beta()

    teardown(_Client(), "agent_123")
    assert calls["archived"] == "agent_123"


# --- гейт вартості: live заблоковано без allow_live --------------------------


def test_live_run_blocked_without_allow_live():
    with pytest.raises(RuntimeError, match="платна зовнішня платформа|заблоковано"):
        run_managed_support(github_token="ghp_x")  # allow_live за замовч. False


def test_live_run_blocked_message_mentions_estimate():
    with pytest.raises(RuntimeError, match="estimate_cost"):
        run_managed_support(github_token="ghp_x", allow_live=False)
