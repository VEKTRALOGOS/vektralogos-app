# Vektralogos — TASKS (єдина дошка статусу)

> **Єдине джерело правди по статусах задач.** Живе в репо на GitHub:
> `VEKTRALOGOS/vektralogos-app`. Оскільки чати трьох AI між собою **не
> спілкуються**, уся координація — тільки через цей файл на GitHub.

## Як кожен AI відмічає статус

**Claude Code** (має прямий доступ до репо):
- Читає й оновлює цей файл напряму. Статус `✅` ставить **лише після merge у
  `main`** (не за факт написання коду в гілці).
- Зміну статусу комітить разом із роботою.

**Claude AI та Gemini** (пушити в репо не можуть — маршрутизує Антон):
- На старті сесії Антон дає їм актуальний `TASKS.md` (файл або raw-URL з GitHub).
- Працюють **тільки над рядками, де вони вказані у стовпці «Хто»**.
- Кожен свій вивід починають шапкою:
  `Target: <шлях у репо>` · `Closes: #<номер рядка>`
  (як уже роблять спеки — напр. `docs/copy/app-store-listing.md`).
- Антон зберігає вивід у репо → статус у `TASKS.md` перемикає Claude Code (або
  Антон вручну).

**Легенда:** `✅` done · `🔄` in-progress · `☐` todo · `⛔` blocked

---

## Дошка

| # | Етап | Задача | Хто | Статус | Нотатка |
|---|---|---|---|---|---|
| 1 | Ф0 | preflight `{ok, issues}` | Claude Code | ✅ | |
| 2 | Ф0 | тест-кейси CanvasJSON | Claude Code | ☐ | |
| 3 | Editor MVP | тонкий HTTP-шар (API) | Claude Code | 🔄 | код готовий на `feat/editor-mvp` (b6b83cf), pushed; live-verified; чекає merge → тоді ✅ |
| 4 | Editor MVP | Fabric.js клієнт (UI) | Claude Code | 🔄 | код готовий на `feat/editor-mvp` (b6b83cf), pushed; прев'ю з того ж CanvasJSON+.ttf; чекає merge → тоді ✅ |
| 5 | Editor MVP | реальні скрін+GIF | Антон | ☐ | залежить від 3,4 |
| 6 | Ресёрч | App Store вимоги 2026 | Gemini | ☐ | у репо ще немає |
| 7 | Ресёрч | pricing-competitors | Gemini | ✅ | `docs/research/pricing-competitors.md` |
| 8 | Ресёрч | 1★ відгуки (реальні) | Gemini | ☐ | лише синтетика у fixtures |
| 9 | Копі | App Store лістинг | Claude AI | ✅ | v1.1 → `docs/copy/app-store-listing.md` |
| 10 | Спека | preflight-агент (Ф1) | Claude AI | ✅ | |
| 11 | Ф1 | Preflight-agent (код) | Claude Code | ✅ | |
| 12 | Ф1 | RAG support-бот | Claude Code | ✅ | |
| 13 | Ф2 | Product Agent (LangGraph) | Claude Code | ✅ | |
| 14 | Ф3 | Director оркестратор | Claude Code | ✅ | |
| 15 | Ф4 | гейти/трейс/ліміти/evals | Claude Code | ✅ | |
| 16 | Ф5 | Managed Agents хостинг | Claude Code | ✅ | |
| 17 | Інфра | Shopify Partner + dev store | Антон | ☐ | |
| 18 | Інфра | Billing API тарифи | Claude Code + Антон | ☐ | тарифи затверджено (DECISIONS) |
| 19 | Інфра | git init + GitHub remote | Claude Code | ✅ | `VEKTRALOGOS/vektralogos-app` |
| 20 | Ops | ця дошка `docs/TASKS.md` | Claude Code | ✅ | |
| 21 | Onboarding | спека флоу (7 екранів) | Claude AI | ✅ | `docs/specs/onboarding-flow.md` |
| 22 | Onboarding | реалізація флоу | Claude Code | 🔄 | код на `feat/onboarding-flow` (поверх Editor MVP); 7 екранів, live-verified; чекає merge → ✅ |
| 23 | Copy | shot-list під зйомку | Claude AI + Claude Code | ✅ | `docs/copy/app-store-shotlist.md` (ClaudeAI shot-list, збережено) |

**Відкрито зараз:** 2, 5, 6, 8, 17, 18 (3–4, 22 у 🔄 — код готовий, чекає merge).
**Гарячий фронт:** merge `feat/editor-mvp` → `feat/onboarding-flow` (закриває 3–4,
22) → 5 (зйомка за `docs/copy/app-store-shotlist.md`) → 6 (Gemini: App Store
вимоги — блокер подачі на ревʼю).

---

## Приватне (не на GitHub)

`STRATEGY.md` і `_ops/` внесені в `.gitignore` (тримаємо локально). ⚠️ Перевірити,
що вони не лишилися в git-**історії** з ранніх комітів (`git ls-files | grep -E
'STRATEGY|_ops'`) — якщо репо публічне, інакше приватні документи вже видно.
