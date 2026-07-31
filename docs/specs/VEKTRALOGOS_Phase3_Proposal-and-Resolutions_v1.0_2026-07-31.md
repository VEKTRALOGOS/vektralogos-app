# Vektralogos — Фаза 3: пропозиція + рекомендовані резолюції (Claude Code → стратег)

**Автор:** Claude Code (виконавець)
**Адресат:** Claude AI (стратег) / Антон
**Дата:** 2026-07-31
**Статус:** пропозиція — чекає на затвердження стратегом/Антоном
**Передумова:** Фаза 2 завершена (PR #3 2a, PR #4 2b вмерджені, 54/54 тести).
**Базується на:** `server/preflight_graph.py`, `server/product_graph.py`,
`server/support_bot.py`, STRATEGY.md §3 Фаза 3 / §4, `docs/DECISIONS.md`.

> Це вхідний артефакт ДО спеки Фази 3 (щоб не пересилати довгий текст у чат).
> Виконавець дає рекомендацію на кожне відкрите питання — стратег затверджує
> або коригує, далі кодимо. Той самий формат, що
> `VEKTRALOGOS_Phase1_Plan-and-Decisions`.

---

## 0. Стан Фази 2 (закрито)

- **2a** (PR #3, `0a77127`) — preflight-агент переписаний на LangGraph
  `StateGraph` без зміни поведінки; еквівалентність з Ф1-функцією доведена
  порівняльним тестом; checkpoints через `MemorySaver`.
- **2b** (PR #4, `f41c8ba`) — Product Agent: `ingest_feedback → plan →
  generate_preset → run_preflight(subgraph 2a) → prepare_diff`. Єдиний
  LLM-вузол — `generate_preset`; `prepare_diff` пише diff на диск без
  `gh pr create`; статус `no_feedback` — чесна зупинка.
- 54/54 тести зелені; live-перевірено (claude-opus-5 + Ghostscript).

**Перевикористовувані блоки, готові для воркерів Ф3:** граф 2а
(`preflight_agent_graph`), `ingest_feedback`/парсер відгуків, retrieval
support-бота (`ask`), детермінований `plan`.

---

## 1. Рекомендовані резолюції по 5 питаннях

| # | Питання | Рекомендація Claude Code | Чому |
|---|---|---|---|
| 1 | Скоуп Ф3 | **Два milestone: 3a — Director + ОДИН воркер (Product, переюз 2b) як каркас делегування; 3b — додати Marketing + Sales/Support.** | Та сама дисципліна малих PR і «не прыгаем вперёд», що спрацювала в 2a/2b. Каркас координатора вчиться окремо від наповнення воркерів. |
| 2 | Комунікація Director↔воркери | **LangGraph subgraphs як вузли** (той самий патерн, що 2b викликає граф 2а) + спільний `DirectorState` TypedDict. Без окремого handoff-протоколу. | Переюз уже засвоєного й протестованого патерну; менше нового API за раз. |
| 3 | Метрики (installs/MRR/rating/churn) | **Синтетичний фікстур зараз** (`docs/research/fixtures/metrics.md\|json`), той самий glob-парсер-підхід, що відгуки в 2b. Реальне джерело — коли з'явиться. | Той самий принцип «не тягнути інфру наперед потреби» (rank_bm25/MemorySaver). Нуль зміни коду при заміні вмісту. |
| 4 | Гейт «валідація спроса перед building» | **Легка ДЕТЕРМІНОВАНА версія у Ф3** (перший вузол Director: є сигнал у метриках/відгуках → делегує Product; нема → `no_signal`). Human-approval-гейти на необоротне — лишаються Ф4. | STRATEGY §4 прямо каже «паттерн гейта перед постройкой освой сразу». Це маршрутизація, не approval-інфра — розділяємо з Ф4. |
| 5 | Marketing / Sales-Support воркери | **Тонкі скелети у 3b:** Sales/Support **переюзає retrieval Ф1 support-бота** (`ask`), Marketing — тонкий LLM-вузол. Повна автономія / Managed Agents — Ф5. | «preflight_subgraph та ingest_feedback — перевикористовувані блоки воркерів» (теза в кінці спеки Ф2). Тримаємо воркери тонкими до Ф5. |

---

## 2. Схема графа Ф3 (як бачить виконавець)

3a — суцільні лінії; 3b додає пунктирні воркери.

```
                    ┌───────────────────────────────────────────┐
                    │           Director / Orchestrator          │
                    │  [validate_demand]  ← гейт перед building   │
                    └───────────────┬───────────────────────────┘
              (сигнал є)            │              (сигналу нема)
                    ▼               │                      ▼
             [route_to_workers]     │              [finalize(no_signal)] → END
                    │
      ┌─────────────┼───────────────────────────────┐
      ▼             ▼ (3b)                            ▼ (3b)
[Product worker]  [Marketing worker]        [Sales/Support worker]
 subgraph 2b       тонкий LLM-вузол           переюз Ф1 support-bot (ask)
      │             │                                 │
      └─────────────┴────────────────┬────────────────┘
                                     ▼
                            [collect_results]   ← агрегація у DirectorState
                                     ▼
                              [finalize] → END
```

**Термінальні статуси** (за аналогією з чесними зупинками Ф1/Ф2):
`ok` / `no_signal` (гейт не пропустив) / `needs_human` (воркер повернув
needs_human, напр. Product→preflight).

---

## 3. Milestone-розбивка

- **3a `feat/p3a-director-product`:** `validate_demand → route_to_workers →
  Product(subgraph 2b) → collect_results → finalize`. Каркас делегування +
  детермінований гейт, один воркер. Acceptance: гейт пропускає/зупиняє за
  фікстуром метрик; Product-воркер реально делегує граф 2b; `no_signal` на
  порожньому сигналі.
- **3b `feat/p3b-director-workers`:** додати Marketing + Sales/Support воркери;
  `collect_results` агрегує всіх трьох. Acceptance: Sales/Support відповідає
  через retrieval Ф1; Marketing дає контент-чернетку; Director зводить
  результати у `DirectorState`.

---

## 4. Що прошу від стратега

Затвердити або скоригувати:
1. П'ять резолюцій §1 (скоуп, комунікація, метрики, гейт, воркери).
2. Розбивку 3a/3b §3 і термінальні статуси §2.
3. Формат фікстура метрик (поля: `installs`, `mrr`, `rating`, `churn`, дата?) —
   щоб `validate_demand`-парсер одразу писався під фінальну форму.

Після затвердження виконавець стартує з `feat/p3a-director-product`, як у Ф2.

---

**Наступний крок після Ф3:** Фаза 4 (`chore/observability`) — approval-гейти
на необоротне (перший кандидат — автостворення PR у `prepare_diff`), трейс,
ретраї, ліміти токенів, evals.
