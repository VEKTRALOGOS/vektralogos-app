"""DesignBrief — семантичний намір клієнта (спека §5, Claude AI).

Розділення відповідальності (гібридний контракт Фази 0):
  * LLM (claude-opus-5) перетворює вільний текст клієнта на DesignBrief —
    стиль, палітра, текстові елементи з ролями, підказка розкладки.
    Модель НЕ рахує міліметрові координати (у цьому LLM слабкі).
  * Детермінований шаблонизатор (server/templater.py) перетворює DesignBrief
    на конкретний Canvas JSON із позиціями. Так тракт лишається end-to-end,
    але раскладка передбачувана.

System prompt — «Оновлений повний» варіант A з
docs/prompts/VEKTRALOGOS_PROMPTS_Phase0-EdgeCases (бренди/порожнє/невідоме).
Порожній/сміттєвий ввід (len<3) обробляємо без виклику API (легкий шорткат).
"""

from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

MODEL = "claude-opus-5"  # дефолт проєкту (DECISIONS.md)

Role = Literal["name", "title", "message", "date"]
LayoutHint = Literal["centered", "left-aligned", "banner", "corner"]

_HEX_RGB = r"^#[0-9A-Fa-f]{6}$"


class BriefTextElement(BaseModel):
    """Реальний вміст для друку. `assumed=True` — коли значення додумане/пусте."""

    model_config = ConfigDict(extra="forbid")
    content: str
    role: Role
    assumed: bool = False


class DesignBrief(BaseModel):
    """Структурований опис дизайну (output schema §5.3)."""

    model_config = ConfigDict(extra="forbid")
    style: str = Field(description="напр. festive, minimal, formal, custom")
    palette: list[str] = Field(
        min_length=1, max_length=3, description="1..3 hex-кольори #RRGGBB"
    )
    text_elements: list[BriefTextElement] = Field(default_factory=list)
    layout_hint: LayoutHint

    def model_post_init(self, __context) -> None:
        import re

        for c in self.palette:
            if not re.match(_HEX_RGB, c):
                raise ValueError(f"palette містить не-hex колір: {c!r}")


# System prompt: варіант A (базові правила + три особливі випадки).
SYSTEM = """Ти — асистент дизайну для персоналізації друкованих товарів (листівки,
гравіювання, мерч). Клієнт магазину описує, що хоче, вільним текстом
(можливо українською, російською або англійською).

Твоя задача: перетворити опис на структурований DesignBrief.

Правила:
- Якщо клієнт не вказав колір/стиль — заповни розумним дефолтом, познач
  "assumed": true для цього поля.
- Текстові поля (імена, підписи) винось у "text_elements" окремо — це
  реальний вміст для друку, не вигадуй і не змінюй орфографію імені клієнта.
- Ніколи не додавай товарні знаки, бренди, символіку третіх осіб.
- Відповідай ЛИШЕ валідним JSON за схемою. Без пояснень, без markdown.

Особливі випадки:

1. БРЕНД/ТОВАРНИЙ ЗНАК ТРЕТЬОЇ ОСОБИ: ігноруй цю частину запиту повністю,
   не згадуй бренд у жодному полі. Побудуй DesignBrief з решти запиту.
   Якщо після вилучення бренду запит порожній — постав "style": "custom",
   "text_elements": [], всі поля "assumed": true.

2. ПОРОЖНІЙ АБО НЕЗРОЗУМІЛИЙ ЗАПИТ: не став помилку. Поверни валідний JSON
   з мінімальним нейтральним дизайном: "style": "custom", 2 нейтральні
   кольори в "palette", "text_elements": [], "layout_hint": "centered",
   всі поля "assumed": true.

3. НЕВІДОМІ ДАНІ (напр. "сьогоднішня дата"): НЕ вигадуй конкретне значення.
   Постав "content": "" і "assumed": true — клієнт заповнить вручну."""


def neutral_default_brief() -> DesignBrief:
    """Нейтральний дефолт для порожнього/сміттєвого вводу (без виклику API)."""
    return DesignBrief(
        style="custom",
        palette=["#333333", "#DDDDDD"],
        text_elements=[],
        layout_hint="centered",
    )


def prompt_to_brief(prompt: str, *, max_tokens: int = 4000) -> DesignBrief:
    """claude-opus-5: вільний текст -> DesignBrief (structured output).

    Легкий шорткат: для len(prompt.strip()) < 3 повертаємо нейтральний дефолт,
    не палячи токени (рекомендація edge-cases, варіант B-lite).
    """
    if len(prompt.strip()) < 3:
        return neutral_default_brief()

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY не заданий (додай у .env)")

    import anthropic

    client = anthropic.Anthropic()
    # structured output через parse-хелпер живе у beta-неймспейсі (anthropic 0.75)
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=DesignBrief,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(
            f"Модель відмовила згенерувати бриф: {getattr(response, 'stop_details', None)}"
        )
    brief = response.parsed_output
    if brief is None:
        raise RuntimeError(
            f"Не вдалося розібрати DesignBrief (stop_reason={response.stop_reason})"
        )
    return brief
