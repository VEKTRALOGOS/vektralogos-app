"""claude-opus-5: «prompt -> Canvas JSON» через structured output (Фаза 0).

Концепт Фази 0 (STRATEGY.md): один LLM-виклик зі structured output. Схема
CanvasJSON (server/schema.py) слугує форматом виводу — модель зобов'язана
повернути валідний Canvas JSON, який далі напряму йде у render().

Секрети — лише з .env (ANTHROPIC_API_KEY); ніколи в коді (guardrail §5).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .schema import CanvasJSON

MODEL = "claude-opus-5"  # дефолт проєкту (DECISIONS.md)

# Шрифти, доступні серверу (server/fonts/). Модель мусить посилатися лише на них.
AVAILABLE_FONTS = [{"family": "Noto Sans", "file": "NotoSans-Regular.ttf"}]

_SYSTEM = f"""Ти — генератор друкарського макета для Vektralogos.
За текстовим запитом користувача поверни ОДИН валідний Canvas JSON (версія "1.0").

Тверді правила:
- Одиниці — міліметри; початок координат — верхній лівий кут (x праворуч, y вниз).
- Для тексту (x_mm, y_mm) — базова лінія першого рядка.
- Використовуй ЛИШЕ доступні шрифти й задай їх у полі fonts: {AVAILABLE_FONTS}.
  Поле font кожного тексту мусить дорівнювати family одного з них.
- Кольори: {{"rgb": "#RRGGBB"}} або {{"cmyk": [c, m, y, k]}} (0..1).
- Растрові зображення (type "image") дозволені лише як фото-зона з is_photo_zone=true.
  Якщо фото не потрібне — не додавай image взагалі.
- Тримай елементи в межах полотна з урахуванням bleed_mm (типово 3 мм).
- Текст пиши мовою запиту (за замовчуванням — українською), кирилиця дозволена."""


def prompt_to_canvas(prompt: str, *, max_tokens: int = 8000) -> CanvasJSON:
    """Викликає claude-opus-5 і повертає валідований CanvasJSON.

    Кидає RuntimeError, якщо модель відмовила (stop_reason == "refusal").
    """
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY не заданий (додай у .env)")

    import anthropic  # локальний імпорт: залежність потрібна лише для цього шляху

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=CanvasJSON,
    )

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RuntimeError(f"Модель відмовила згенерувати макет: {details}")

    canvas = response.parsed_output
    if canvas is None:  # напр. урвано по max_tokens
        raise RuntimeError(
            f"Не вдалося розібрати Canvas JSON (stop_reason={response.stop_reason})"
        )
    return canvas
