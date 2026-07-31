"""Acceptance-прогін 10 тест-промптів (спека §6, docs/prompts/…TestPrompts).

Для кожного промпту: prompt_to_brief -> DesignBrief|refusal|error, потім
brief_to_canvas -> render_vector_pdf (без винятків). Друкує звіт.

УВАГА: робить реальні виклики claude-opus-5 (витрачає кредити API).
Потрібен ANTHROPIC_API_KEY у .env. Запуск:

    PYTHONPATH=. .venv/bin/python scripts/run_test_prompts.py
"""

from __future__ import annotations

import traceback

from server.brief import prompt_to_brief
from server.prompt_to_canvas import brief_from_prompt_to_canvas
from server.render import render_vector_pdf

# (номер, мова, промпт) — з VEKTRALOGOS_PROMPTS_Phase0-TestPrompts.
PROMPTS = [
    (1, "укр", "Листівка з іменем Іван, синьо-золота, святкова"),
    (2, "укр", 'Гравіювання на кухлі: "З Днем Народження, Мамо!"'),
    (3, "рос", "Хочу открытку, ничего конкретного, на ваш вкус"),
    (4, "eng", "Wedding invitation for Sarah & Tom, elegant, gold and ivory, date June 14 2027"),
    (5, "укр", 'Вивіска "Кав\'ярня Їжачок" з ґуля-шрифтом'),
    (6, "рос", "Сделай мне логотип Coca-Cola на кружке"),
    (7, "eng", "asdf qwerty 12345"),
    (8, "укр", ""),  # порожній -> шорткат len<3, без API
    (9, "eng", "Birthday card for my dad, he loves fishing, blue tones, add 'World's Best Dad' text and today's date"),
    (10, "рос", "Табличка на дверь кабинета: Петров Иван Сергеевич, главный врач"),
]


def short(s: str, n: int = 60) -> str:
    return (s[: n - 1] + "…") if len(s) > n else s


def main() -> None:
    for num, lang, prompt in PROMPTS:
        print("=" * 78)
        print(f"#{num} [{lang}] {short(prompt) or '(порожній)'}")
        try:
            brief = prompt_to_brief(prompt)
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ prompt_to_brief EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            continue

        print(f"  style       = {brief.style!r}")
        print(f"  palette     = {brief.palette}")
        print(f"  layout_hint = {brief.layout_hint}")
        if brief.text_elements:
            for t in brief.text_elements:
                flag = " (assumed)" if t.assumed else ""
                print(f"    - {t.role:8s}: {t.content!r}{flag}")
        else:
            print("    (text_elements порожній)")

        try:
            spec = brief_from_prompt_to_canvas(brief, size="a6")
            pdf = render_vector_pdf(spec)
            ok = pdf.startswith(b"%PDF") and b"/FontFile" not in pdf
            print(f"  render      = ✅ {len(pdf)} байт, outlined={ok}")
        except Exception as e:  # noqa: BLE001
            print(f"  render      = ❌ {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
