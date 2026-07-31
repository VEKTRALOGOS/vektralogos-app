"""Надійність LLM-викликів: обмежені ретраї + per-run token budget (Фаза 4b).

Скоуп навмисно вузький (спека §1, п.6; STRATEGY §4 «контроль вартості»):
  * `retry()` — обмежена кількість спроб на транзієнтний збій виклику;
  * `TokenBudget` — лічильник токенів на прогін зі стопом при перевищенні.

Бюджет прокидається через `contextvars` (а не через сигнатури всіх LLM-вузлів)
— виклик-сайти лише «звітують» витрату через `account(response)`, а активний
бюджет задає верхній рівень (`budget_scope`). Немає активного бюджета —
`account` це no-op, тобто дефолтна поведінка не змінюється.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")


# --- ретраї ------------------------------------------------------------------


def retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    base_delay: float = 0.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Викликає `fn` до `attempts` разів; на перших невдачах — ретрай, на
    останній — прокидає виняток далі. `base_delay` — лінійний backoff (сек)."""
    if attempts < 1:
        raise ValueError("attempts має бути >= 1")
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except exceptions as exc:  # noqa: PERF203 — ретрай-петля навмисна
            last = exc
            if i == attempts:
                raise
            if on_retry is not None:
                on_retry(i, exc)
            if base_delay:
                time.sleep(base_delay * i)
    assert last is not None  # недосяжно
    raise last


# --- token budget ------------------------------------------------------------


class BudgetExceeded(RuntimeError):
    """Перевищено per-run ліміт токенів — прогін зупиняється (спека §1 п.6)."""


class TokenBudget:
    """Лічильник токенів на один прогін. `record()` додає витрату відповіді
    Anthropic і кидає `BudgetExceeded`, якщо ліміт перевищено."""

    def __init__(self, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens має бути > 0")
        self.max_tokens = max_tokens
        self.used = 0

    def add(self, tokens: int) -> None:
        self.used += max(0, tokens)
        if self.used > self.max_tokens:
            raise BudgetExceeded(
                f"Перевищено token budget: {self.used} > {self.max_tokens}"
            )

    def record(self, response: object) -> None:
        """Дістає usage з відповіді Anthropic (input+output tokens) і додає."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        total = (getattr(usage, "input_tokens", 0) or 0) + (
            getattr(usage, "output_tokens", 0) or 0
        )
        self.add(total)


_active_budget: contextvars.ContextVar[TokenBudget | None] = contextvars.ContextVar(
    "active_budget", default=None
)


@contextmanager
def budget_scope(max_tokens: int) -> Iterator[TokenBudget]:
    """Активує TokenBudget на час блоку; виклик-сайти LLM його підхоплять."""
    budget = TokenBudget(max_tokens)
    token = _active_budget.set(budget)
    try:
        yield budget
    finally:
        _active_budget.reset(token)


def account(response: object) -> None:
    """Звітує витрату відповіді у активний бюджет (no-op, якщо його нема)."""
    budget = _active_budget.get()
    if budget is not None:
        budget.record(response)
