# Vektralogos — Фаза 5: пропозиція + рекомендовані резолюції (Claude Code → стратег)

**Автор:** Claude Code (виконавець)
**Адресат:** Claude AI (стратег) / Антон
**Дата:** 2026-07-31
**Статус:** пропозиція — чекає на затвердження стратегом/Антоном
**Передумова:** Фази 0–4 завершені й у `main` (PR #1–#8, 81/81 тестів).
**Базується на:** STRATEGY.md §3 Фаза 5 / §5, `server/support_bot.py`,
`server/director_graph.py`, `docs/DECISIONS.md`, актуальний API Claude Managed
Agents (перевірено через claude-api skill, beta `managed-agents-2026-04-01`).

> Вхідний артефакт ДО спеки Ф5 (фінальна фаза). Виконавець дає рекомендацію на
> кожне питання; стратег затверджує/коригує. Формат — як Ф3/Ф4.

---

## 0. Що таке Ф5 і чому це фінал

STRATEGY §3 Фаза 5: **перенести відлагоджену Support/Ops-петлю на Claude
Managed Agents (CMA)** — нативний рантайм, де Anthropic хостить і сам цикл
агента, і пісочницю з інструментами (bash/файли/код), сесії, розклад. Мета —
навчальна: **порівняти самописну LangGraph-петлю (Ф2–Ф4) з managed-платформою**
і зрозуміти, коли «своє» vs «managed» (DECISIONS: Tool Runner/CMA лишали на Ф5).

**Ключова відмінність, підтверджена API:** LangGraph (наше) = ми пишемо і
хостимо і петлю, і виконання інструментів. CMA = Anthropic хостить **і петлю, і
пісочницю**; ми лише задаємо Agent-конфіг (model/system/tools) і стрімимо події.
Це і є предмет порівняння.

---

## 1. Рекомендовані резолюції

| # | Питання | Рекомендація Claude Code | Чому |
|---|---|---|---|
| 1 | Яку петлю переносити | **Support doc-QA (Ф1 support-bot) першою.** Це найчистіший кандидат: чистий retrieval+відповідь, без Ghostscript і нашого Python-тракту. Preflight/Product-петлі — стретч (§ milestone 5b), бо потребують нашого коду й gs. | Мінімальний, чесний перший крок на нову платформу; вивчаємо Agent→Session→Environment на відомому домені (як 2a вивчав LangGraph на preflight). |
| 2 | Cloud чи self-hosted пісочниця | **Cloud для 5a** (repo mount + вбудовані `read`/`grep`/`glob` роблять retrieval нативно, gs не треба). **Self-hosted АБО host-side custom tools для 5b**, бо друкарський тракт потребує нашого `render()`+Ghostscript, яких немає в cloud-контейнері. | «Не тягнути інфру наперед потреби»: cloud там, де вистачає; self-hosted лише коли реально потрібні наші бінарники. |
| 3 | Як віддати наш `preflight`/`render` агенту (5b) | **Host-side custom tools** (Pattern 9 CMA): агент емітить `agent.custom_tool_use`, наш оркестратор виконує `preflight_agent`/`render` у нас і повертає `user.custom_tool_result`. Секрети й бінарники лишаються в нас. | Переюз ГОТОВИХ Ф1–Ф4 функцій без переписування під пісочницю; той самий принцип, що `run_preflight` делегує граф 2а. |
| 4 | Розклад (Ops-автономність) | **Scheduled deployment (`deployments.create` з cron)** у 5b — нативний CMA-аналог «агент за розкладом» (STRATEGY §4 автономний ops). Кожне спрацювання створює сесію. | STRATEGY §3 Ф5 прямо називає «розклад»; CMA дає це нативно, без нашого шедулера. |
| 5 | Що є навчальним артефактом | **Порівняльний ADR у `docs/DECISIONS.md`: LangGraph vs CMA** — хто хостить петлю, хто пісочницю, вартість, спостережність, версіювання, коли що. | Це і є «результат фази» за STRATEGY (концепт+робочий результат); переносимо знання, не лише код. |
| 6 | Модель | **`claude-opus-5`** для CMA-агента (дефолт проєкту, DECISIONS). | Консистентно з усім проєктом. |

---

## 2. Схема (як бачить виконавець)

**5a — Support doc-QA на CMA (cloud):**
```
[agents.create] (once, versioned: model=claude-opus-5, system, tools=agent_toolset)
        │
        ▼
[sessions.create] --resources=[github_repository: vektralogos-app] --> container
        │  (Anthropic хостить петлю + пісочницю)
        ▼
user.message("чому CMYK через Ghostscript?")
        │  агент сам: grep/read по репо -> відповідь з джерелами
        ▼
stream events -> agent.message  (порівнюємо з Ф1 BM25 `ask`)
```

**5b — Print/Ops-петля на CMA (self-hosted / host-side tools, + розклад):**
```
[agents.create] tools=[custom: run_preflight, custom: render_pdf]
        │
[deployments.create] schedule=cron  ──(кожне спрацювання)──> [sessions.create]
        │
agent.custom_tool_use(run_preflight, spec)  ──> НАШ оркестратор виконує
        │                                        preflight_agent()/render() у нас
        └──< user.custom_tool_result(звіт) <──  (gs і код лишаються host-side)
```

Термінальні стани CMA-сесії: `idle`(stop_reason) / `terminated` — мапимо на наші
`ok`/`needs_human` у звіті порівняння.

---

## 3. Milestone-розбивка

- **5a `feat/p5a-support-managed-agent`:** Support doc-QA як CMA cloud-агент;
  repo примонтований, retrieval через вбудовані інструменти. Скрипт
  `server/managed_support.py` (create-agent-once → session → stream) + порівняння
  відповідей з Ф1 `ask` на тих самих питаннях. Agent-ID у конфіг, не в hot-path.
- **5b `feat/p5b-managed-ops` (стретч):** `run_preflight`/`render` як host-side
  custom tools; scheduled deployment для ops-кадансу. Порівняльний ADR
  LangGraph vs CMA у DECISIONS.

**Acceptance (5a):** агент відповідає на контрольні питання з джерелами;
порівняння з Ф1 задокументоване; `agents.create` — один раз, ID збережений
(не викликається в hot-path — інваріант CMA).

---

## 4. Що прошу від стратега + ⚠️ гейт вартості

Затвердити/скоригувати:
1. Резолюції §1 (яку петлю, cloud/self-hosted, custom-tools, розклад, артефакт).
2. Розбивку 5a/5b §3.

> [!IMPORTANT] ⚠️ **Гейт вартості/зовнішніх дій — потрібне явне «так» Антона.**
> На відміну від Ф0–Ф4 (локальні тести + наш GitHub), CMA — **платна зовнішня
> платформа**: live-сесії тарифікуються, створюють зовнішні ресурси (agents/
> sessions/deployments) в акаунті Anthropic. Standing-autonomy на це НЕ
> поширюється. Код і тести напишу без live-запусків; перед першим реальним
> прогоном CMA-сесії — окреме підтвердження Антона (скільки прогонів, який
> бюджет). Це узгоджено з тим, що сама Ф4 будувала approval-гейти на необоротне.

---

**Наступний крок після Ф5:** фаз більше немає — це фінал навчальної програми
STRATEGY §3. Далі — продуктова робота (лістинг App Store, перші установки) поза
рамками фазового плану.
