"""Тести ретраїв, token budget і evals (Фаза 4b, спека §1 п.5-6, §4)."""

from __future__ import annotations

import pytest

from server.evals import run_evals
from server.reliability import (
    BudgetExceeded,
    TokenBudget,
    account,
    budget_scope,
    retry,
)


# --- ретраї (acceptance: спрацьовує на збою, здається після N) ---------------


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert retry(flaky, attempts=3, exceptions=(ValueError,)) == "ok"
    assert calls["n"] == 3  # 2 збої + 1 успіх


def test_retry_gives_up_after_n_attempts():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        retry(always_fail, attempts=3, exceptions=(ValueError,))
    assert calls["n"] == 3  # рівно N спроб, далі прокидає


def test_retry_does_not_catch_unlisted_exception():
    def fail():
        raise KeyError("not retried")

    with pytest.raises(KeyError):
        retry(fail, attempts=3, exceptions=(ValueError,))


def test_retry_on_retry_callback_fires():
    seen = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ValueError("x")
        return 1

    retry(flaky, attempts=3, exceptions=(ValueError,), on_retry=lambda i, e: seen.append(i))
    assert seen == [1]  # один ретрай перед успіхом


# --- token budget (acceptance: стоп при перевищенні) -------------------------


class _Resp:
    def __init__(self, in_t, out_t):
        self.usage = type("U", (), {"input_tokens": in_t, "output_tokens": out_t})()


def test_budget_accumulates_and_stops():
    b = TokenBudget(max_tokens=100)
    b.record(_Resp(30, 20))  # 50
    assert b.used == 50
    with pytest.raises(BudgetExceeded):
        b.record(_Resp(40, 30))  # 50+70=120 > 100


def test_budget_scope_accounts_via_contextvar():
    # account() поза scope — no-op; всередині scope — рахує й може зупинити
    account(_Resp(10, 10))  # поза scope: нічого не кидає
    with budget_scope(max_tokens=15) as b:
        with pytest.raises(BudgetExceeded):
            account(_Resp(10, 10))  # 20 > 15
        assert b.used == 20


def test_budget_zero_or_negative_rejected():
    with pytest.raises(ValueError):
        TokenBudget(0)


# --- evals (acceptance: набір зелений на фікстурах) --------------------------


def test_all_evals_pass_on_fixtures():
    results = run_evals()
    assert len(results) == 3
    failed = [r.name for r in results if not r.passed]
    assert not failed, f"впали evals: {failed}"


def test_evals_have_expected_names():
    names = {r.name for r in run_evals()}
    assert names == {
        "preflight_converges",
        "preset_valid_and_printable",
        "gate_signal_on_fixtures",
    }
