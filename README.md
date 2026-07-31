# Vektralogos

Shopify-застосунок для кастомізації товарів, що віддає **коректний друкарський
вектор** (SVG→PDF, CMYK, 300 DPI, кирилиця через text-to-outlines). Дві цілі:
дохід (Shopify Billing, ніша з підтвердженим WTP) + навчання мультиагентним
системам на LangGraph.

## Документи
- `CLAUDE.md` — гайд для розробки (стек, архітектура, інваріанти).
- `docs/prompts/` — спека Фази 0 та промпти.
- `docs/DECISIONS.md` — журнал рішень (ADR-lite).

> Стратегія та операційна модель (`STRATEGY.md`, `_ops/`) тримаються приватно,
> поза цим публічним репозиторієм.

## Структура
```
server/     # Python pipeline (JSON → SVG → outlines → vector PDF → Ghostscript CMYK)
client/     # Fabric.js редактор (браузер)
docs/       # research / specs / prompts + DECISIONS.md
examples/   # приклади Canvas JSON
tests/
```

## Старт (Фаза 0 — заповнюється)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# make render SPEC=examples/hello.json
```

Модель у коді — `claude-opus-5`. Сервер — тільки Python.
