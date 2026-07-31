# CLAUDE.md

> Керівництво для Claude Code (і людей), що працюють у репозиторії Vektralogos.

## 1. Огляд і мета

**Vektralogos** (Print-Ready Personalizer) — Shopify-застосунок для кастомізації товарів, що
віддає **коректний друкарський вектор** (SVG→PDF, CMYK, 300 DPI, кирилиця через
text-to-outlines). Дві цілі: **дохід** (Shopify Billing, ніша з підтвердженим WTP)
і **навчання** мультиагентним системам (LangGraph). Повна стратегія й фази —
`STRATEGY.md`.

## 2. Стек

| Шар | Технологія |
|---|---|
| Клієнт (редактор) | Fabric.js (браузер, JS) — тільки прев'ю |
| Сервер (pipeline) | **Python** (єдина мова серверу) |
| Друкарський PDF | fonttools (text→outlines) → Ghostscript (CMYK/ICC/PDF-X) |
| LLM | Claude API, `claude-opus-5` |
| Оркестрація (з Фази 2) | LangGraph (Python) |
| БД / стейт | Supabase (Postgres) |
| Білінг / дистрибуція | Shopify Billing API + App Store |

## 3. Архітектура (тракт Фази 0)

```
Fabric.js ──export──> Canvas JSON ──┐   Claude Opus 5 (prompt→JSON) ──┐
                                    ▼                                  ▼
Python: JSON → SVG → text-to-outlines → vector PDF → Ghostscript(CMYK+ICC+PDF/X)
                                    ▼
                          Preflight (DPI / bleed / CMYK / розмір)
```

## 4. Тверді інваріанти (guardrails)

- **Єдине джерело правди — Canvas JSON.** Клієнт і сервер парсять ОДИН і той
  самий JSON і використовують ОДНІ й ті самі `.ttf`. Ніколи не дублювати логіку
  рендеру двома шляхами.
- **Друкарський файл = вектор, ніколи не растр.** `resvg` — тільки для прев'ю-
  мініатюр, не для print-PDF.
- **Текст → криві (outlines) за замовчуванням** на етапі складання PDF. Це вбиває
  весь клас багів зі шрифтами/кирилицею. Вбудовування шрифтів — лише опція.
- **CMYK/PDF-X через Ghostscript** з ICC (FOGRA39/SWOP). Не покладатися на «native
  CMYK» бібліотек рендеру.
- **Растрові зображення** — тільки в явно позначену «фото-зону», ніколи не
  авто-векторизувати лайн-арт.

## 5. Стиль коду

- Python: типізація, явні return-типи на публічних функціях, докстрінги.
- Секрети — тільки в `.env` (у `.gitignore`), ніколи в коді/репо.
- Помилки pipeline кидають `Error` з контекстом; шар API транслює у JSON.
- Тести на кожен інваріант розділу 4 (напр. «текст справді в кривих»,
  «PDF справді CMYK»).

## 6. Команди (заповнити при скаффолді)

```bash
# python -m venv .venv && source .venv/bin/activate
# pip install -r requirements.txt
# make render SPEC=examples/hello.json   # JSON → print.pdf
# pytest
```

## 7. Дорожня карта фаз

P0 pdf-pipeline · P1 preflight-agent · P2 product-graph (LangGraph) ·
P3 director-orchestrator · P4 observability/гейти · P5 managed-agents.
Деталі — `STRATEGY.md`.
