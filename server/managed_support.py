"""Support doc-QA на Claude Managed Agents (Фаза 5a, фінал).

Переносить Ф1 support-бота (RAG по репо) на нативний рантайм CMA: Anthropic
хостить і цикл агента, і пісочницю (bash/read/grep/glob). Замість нашого BM25
(`support_bot.ask`) агент сам робить retrieval вбудованими інструментами по
примонтованому репозиторію — а ми порівнюємо результат із самописною петлею.

Ключова відмінність (предмет порівняння §5 спеки):
  * LangGraph (Ф2–Ф4): МИ пишемо й хостимо і петлю, і виконання інструментів.
  * CMA: Anthropic хостить і петлю, і пісочницю; ми лише задаємо Agent-конфіг
    (model/system/tools) і стрімимо події.

⚠️ ВАРТІСТЬ (спека §2): CMA — платна зовнішня платформа ($0.08/session-hour +
токени claude-opus-5). Live-прогони цього модуля вимагають ЯВНОГО «go» Антона з
конкретною оцінкою — див. `estimate_cost()` і прапор `allow_live` у
`run_managed_support()`. `build_agent_config`/`compare_answers`/`estimate_cost`/
`teardown` — детерміновані, без мережі, тестуються офлайн.

Примітка: live-шлях потребує anthropic SDK з підтримкою CMA
(`client.beta.agents`/`sessions`, beta `managed-agents-2026-04-01`) — на go-кроці
оновити залежність; поточний пін лишаємо для Ф0–Ф4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .support_bot import SYSTEM as _PHASE1_SYSTEM  # тон/правила Ф1 переюзаємо
from .support_bot import ask as _phase1_ask

MODEL = "claude-opus-5"  # дефолт проєкту (DECISIONS.md)
REPO_URL = "https://github.com/VEKTRALOGOS/vektralogos-app"
AGENT_NAME = "Vektralogos Support doc-QA"

# Контрольні питання (спека §3 acceptance) — ті самі для CMA і Ф1, щоб порівняння
# було чесним. Останнє — свідомо поза корпусом (перевірка чесної відмови).
CONTROL_QUESTIONS: list[str] = [
    "Яка канонічна CanvasJSON схема і де вона реалізована?",
    "Чому CMYK робимо через Ghostscript, а не native ReportLab?",
    "Чому текст переводиться в криві (outlines)?",
    "Чому SVG немає у друкарському тракті?",
    "Яка ціна підписки на Zepto?",  # поза корпусом -> має бути «немає в доці»
]

# System для CMA-агента: правила Ф1 + вказівка користуватись файловими
# інструментами по репо (замість нашого BM25 retrieval).
CMA_SYSTEM = (
    _PHASE1_SYSTEM
    + "\n\nІнструменти: у тебе є доступ до репозиторію проєкту через файлові "
    "інструменти (grep, read, glob). Знаходь релевантні .md/код самостійно і "
    "спирайся ЛИШЕ на знайдене в репо. Вказуй файл-джерело у відповіді."
)


# --- конфіг агента (чиста функція, тестується без мережі) ---------------------


def build_agent_config(*, model: str = MODEL, name: str = AGENT_NAME) -> dict:
    """Параметри для `client.beta.agents.create(**config)` (створюється ОДИН раз,
    версіюється; ID зберігається в конфіг, не викликається в hot-path — інваріант
    CMA)."""
    return {
        "name": name,
        "model": model,
        "system": CMA_SYSTEM,
        "tools": [{"type": "agent_toolset_20260401"}],  # bash/read/grep/glob/...
    }


def session_resources(*, repo_url: str = REPO_URL, token: str) -> list[dict]:
    """`resources` для сесії: примонтувати репо (read-only clone) у пісочницю."""
    return [{
        "type": "github_repository",
        "url": repo_url,
        "authorization_token": token,
        "checkout": {"type": "branch", "name": "main"},
    }]


# --- оцінка вартості (спека §2.2 — конкретні числа для «go», не відкрите питання)


@dataclass
class CostEstimate:
    n_questions: int
    minutes_per_session: float
    session_hour_rate_usd: float
    est_input_tokens_per_q: int
    est_output_tokens_per_q: int
    input_rate_per_mtok: float
    output_rate_per_mtok: float

    @property
    def session_cost(self) -> float:
        return (self.minutes_per_session / 60.0) * self.session_hour_rate_usd

    @property
    def token_cost(self) -> float:
        inp = self.n_questions * self.est_input_tokens_per_q / 1e6 * self.input_rate_per_mtok
        out = self.n_questions * self.est_output_tokens_per_q / 1e6 * self.output_rate_per_mtok
        return inp + out

    @property
    def total(self) -> float:
        return self.session_cost + self.token_cost

    def summary(self) -> str:
        return (
            f"Оцінка live-прогону 5a (порівняння CMA vs Ф1):\n"
            f"  питань: {self.n_questions}, ~{self.minutes_per_session:.0f} хв сесія\n"
            f"  session-hour: ${self.session_cost:.4f} (тариф ${self.session_hour_rate_usd}/год)\n"
            f"  токени claude-opus-5 (~{self.est_input_tokens_per_q}in/"
            f"{self.est_output_tokens_per_q}out на питання): ${self.token_cost:.3f}\n"
            f"  РАЗОМ ≈ ${self.total:.2f} за весь прогін порівняння"
        )


def estimate_cost(
    *,
    n_questions: int = len(CONTROL_QUESTIONS),
    minutes_per_session: float = 3.0,
    session_hour_rate_usd: float = 0.08,
    est_input_tokens_per_q: int = 15000,  # агент читає кілька файлів репо на питання
    est_output_tokens_per_q: int = 2000,
    input_rate_per_mtok: float = 5.0,  # claude-opus-5 (skill: $5/$25 за MTok)
    output_rate_per_mtok: float = 25.0,
) -> CostEstimate:
    """Прозора оцінка вартості одного порівняльного прогону. Числа-припущення
    явні, щоб Антон підтверджував СУМУ, а не абстрактний «бюджет» (спека §2.2)."""
    return CostEstimate(
        n_questions=n_questions,
        minutes_per_session=minutes_per_session,
        session_hour_rate_usd=session_hour_rate_usd,
        est_input_tokens_per_q=est_input_tokens_per_q,
        est_output_tokens_per_q=est_output_tokens_per_q,
        input_rate_per_mtok=input_rate_per_mtok,
        output_rate_per_mtok=output_rate_per_mtok,
    )


# --- порівняння CMA vs Ф1 (чиста логіка; cma_answer інжектується) -------------


@dataclass
class Comparison:
    question: str
    cma_answer: str
    phase1_answer: str


def compare_answers(
    cma_answer: Callable[[str], str],
    *,
    questions: list[str] = CONTROL_QUESTIONS,
    phase1_answer: Callable[[str], str] = _phase1_ask,
) -> list[Comparison]:
    """Для кожного питання збирає відповідь CMA-агента і Ф1 `ask` поряд.

    `cma_answer` — сім: у тестах фейк, у live — обгортка над CMA-сесією
    (`answer_via_session`). `phase1_answer` дефолтом = наш BM25 `ask`.
    """
    return [
        Comparison(q, cma_answer(q), phase1_answer(q))
        for q in questions
    ]


# --- teardown (спека §2.3 — обов'язкова гігієна ресурсів) ---------------------


def teardown(client, agent_id: str) -> None:
    """Архівує тестовий CMA-агент після порівняння (agents мають лише archive,
    не delete — CMA API). Versioned config сам не тарифікується, тарифікуються
    сесії; архівуємо, щоб нові сесії його не використовували (спека §2.3)."""
    client.beta.agents.archive(agent_id)


# --- live-шлях (ГЕЙТ: лише після явного «go» Антона, §2.1–2.2) ----------------


def run_managed_support(
    *,
    github_token: str,
    allow_live: bool = False,
    questions: list[str] = CONTROL_QUESTIONS,
    do_teardown: bool = True,
) -> list[Comparison]:
    """Live-порівняння CMA vs Ф1. НЕ запускати без `allow_live=True`, який
    ставиться лише після підтвердженої Антоном оцінки (`estimate_cost`).

    Створює агент (один раз), сесію з примонтованим репо, шле контрольні питання,
    стрімить відповіді, порівнює з Ф1, і (за замовч.) архівує агент (§2.3).
    """
    if not allow_live:
        raise RuntimeError(
            "Live-прогін CMA заблоковано. Це платна зовнішня платформа "
            "(спека §2): підтверди оцінку `estimate_cost().summary()` в Антона, "
            "онови anthropic SDK до CMA-версії, і виклич з allow_live=True."
        )

    import uuid

    import anthropic

    client = anthropic.Anthropic()
    agent = client.beta.agents.create(**build_agent_config())
    environment = client.beta.environments.create(
        name=f"vektralogos-support-{uuid.uuid4().hex[:8]}",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    # ОДНА сесія з примонтованим репо на всі питання (дешевше, § оцінки вартості).
    session = client.beta.sessions.create(
        agent={"type": "agent", "id": agent.id, "version": agent.version},
        environment_id=environment.id,
        resources=session_resources(token=github_token),
    )

    def _answer_via_session(question: str) -> str:
        parts: list[str] = []
        with client.beta.sessions.events.stream(session_id=session.id) as stream:
            client.beta.sessions.events.send(
                session_id=session.id,
                events=[{"type": "user.message",
                         "content": [{"type": "text", "text": question}]}],
            )
            for event in stream:
                if event.type == "agent.message":
                    parts.extend(b.text for b in event.content if b.type == "text")
                elif event.type == "session.status_idle":
                    break
                elif event.type == "session.status_terminated":
                    break
        return "".join(parts).strip()

    try:
        return compare_answers(_answer_via_session, questions=questions)
    finally:
        if do_teardown:
            # §2.3 — прибираємо створені ресурси, щоб нічого не тарифікувалось мовчки.
            try:
                client.beta.sessions.archive(session.id)
            except Exception:  # noqa: BLE001
                pass
            try:
                client.beta.environments.delete(environment.id)
            except Exception:  # noqa: BLE001
                pass
            teardown(client, agent.id)
