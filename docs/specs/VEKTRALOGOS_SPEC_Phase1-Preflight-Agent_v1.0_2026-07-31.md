# Vektralogos — Спека Фази 1: Preflight-агент (ReAct-петля)

**Гілка:** `feat/p1-preflight-agent`
**Автор спеки:** Claude AI (стратег/промпт-інженер)
**Виконавець:** Claude Code
**Дата:** 2026-07-31
**Базується на:** актуальному `main` — `server/preflight.py`, `server/render.py`,
`server/cli.py`, `server/schema.py`, `docs/specs/VEKTRALOGOS_SPEC_Phase0-PDF-Pipeline_v1.1_2026-07-31.md`,
`docs/DECISIONS.md`.

> [!NOTE]
> Фаза 0 фактично завершена: `preflight()` (функція, не агент), FOGRA39 ICC
> через `cmyk.py`/`server/icc/`, повний CLI (`render` / `prompt` / `preflight`)
> вже в коді. Ця спека не змінює жоден з інваріантів §4 CLAUDE.md — вона додає
> агентний шар НАД існуючою `preflight()`, не замінюючи її.

---

## 1. Концепт фази (STRATEGY.md §3, Фаза 1)

**Агент з інструментами в циклі (ReAct).** Різниця з Фазою 0: там `preflight()`
— чиста функція, яка ЛИШЕ звітує (`{ok, issues}`), нічого не виправляє. Фаза 1
додає агента, який отримує цей звіт, обирає інструмент для конкретної
проблеми, застосовує його, повторює перевірку — і так по колу, поки або
`ok=True`, або немає більше що виправити.

> [!IMPORTANT]
> Оригінальний список інструментів у STRATEGY.md («check_dpi, check_cmyk,
> vectorize, embed_fonts») писався ДО реалізації Фази 0 і вже не відповідає
> коду: `embed_fonts` не потрібен (текст завжди в кривих, вбудовування шрифтів
> не використовується — інваріант §4 CLAUDE.md), `vectorize` суперечить
> guardrail'у «не авто-векторизувати растр». Розділ 3 нижче замінює цей список
> реальними інструментами під фактичні `issues[].code` з `preflight.py`.

## 2. Скоуп

**В скоупі:**
- Агентний цикл навколо `preflight()`: читає `PreflightReport.issues`, для
  кожного `warn`/`error` з фіксованого списку кодів обирає й викликає
  детермінований fix-інструмент, повторює `preflight()`, зупиняється за
  умовами §4.
- Fix-інструменти для issue-кодів, які реально виправні редагуванням Canvas
  JSON: `bleed_too_small`, `out_of_media`, `low_dpi`.
- Явна, чесна поведінка для issue-кодів, які агент НЕ може виправити
  редагуванням спеки (`rgb_in_print`, `no_pdfx_intent`, `not_pdf`,
  `image_unreadable`) — розділ 5.
- Мінімальний RAG support-бот по документації (розділ 7) — паралельно,
  не блокує основний агентний цикл.

**Поза скоупом (навмисно, пізніші фази):**
- LangGraph/стейт-граф — це Фаза 2. Фаза 1 — простий Python-цикл
  (`while` з лічильником), без оркестрації.
- Cost/token-ліміти, ретраї, трейс, evals — Фаза 4 (`chore/observability`).
  Тут — тільки базовий `max_iterations` як запобіжник від нескінченного циклу
  (це гігієна коду, не повноцінний контроль вартості).
- Генеративна донасичення фото (upscale через AI) — не робимо; `low_dpi`
  виправляємо геометрично (§3.3), не пересемплюємо піксель.

## 3. Fix-інструменти

Кожен інструмент — чиста функція `(CanvasJSON, PreflightIssue) -> CanvasJSON`,
без побічних ефектів, без LLM-виклику. Агентність тут — у ЦИКЛІ й ВИБОРІ
інструмента за кодом issue, не у генерації правок.

### 3.1 `fix_bleed` — код `bleed_too_small`

```python
def fix_bleed(spec: CanvasJSON, min_bleed_mm: float = 3.0) -> CanvasJSON:
    """canvas.bleed_mm = max(поточний, min_bleed_mm). Один issue -> один виклик."""
```

Детерміновано, завжди усуває цей конкретний issue за один прохід.

### 3.2 `clamp_to_media` — код `out_of_media`

```python
def clamp_to_media(spec: CanvasJSON, element_index: int) -> CanvasJSON:
    """Зсуває елемент [element_index] так, щоб влізти в [-bleed..w+bleed]×
    [-bleed..h+bleed], зберігаючи його width/height. Текстові елементи
    (тільки якір x_mm,y_mm) — зсуваються як точка."""
```

`element_index` беремо з тексту `PreflightIssue.message` (`elements[{idx}]`,
формат вже є в `preflight.py`) — розпарсити індекс регексом, не переписувати
`preflight.py` заради структурованого поля (мінімальна зміна).

> [!TODO] Антон/Claude Code: чи додати `element_index: int | None` як окреме
> поле в `PreflightIssue` замість парсингу з `message`? Дешевше і надійніше
> для агента, ламає тільки внутрішній контракт `preflight.py` (публічний API
> `{ok, issues}` не міняється). Рекомендація: так, додати поле — парсинг
> рядків для машинного споживання це технічний борг з першого дня.

### 3.3 `shrink_photo_zone` — код `low_dpi`

```python
def shrink_photo_zone(spec: CanvasJSON, element_index: int, min_dpi: int = 300) -> CanvasJSON:
    """Зменшує width_mm/height_mm фото-зони (зберігаючи aspect ratio, якір
    x_mm,y_mm лишається) до розміру, за якого фактичний DPI растра >= min_dpi.
    НЕ чіпає сам файл зображення — тільки розмір розміщення на полотні."""
```

**Свідоме архітектурне рішення:** не апскейлимо піксель (це вигадування даних,
які піде на друк), а зменшуємо зону розміщення до розміру, який растр реально
підтримує на 300 DPI. Це відповідає інваріанту §4 CLAUDE.md («не
авто-векторизувати растр») за духом — не редагуємо вміст зображення.

> [!TODO] Антон: якщо зменшена фото-зона ламає композицію (наприклад,
> накладається на текст) — Фаза 1 цього не перевіряє (colision detection —
> поза скоупом). Ок для Фази 1 чи потрібен overlap-check зараз?

## 4. Цикл агента

```python
def preflight_agent(spec: CanvasJSON, *, max_iterations: int = 5) -> AgentResult:
    for i in range(max_iterations):
        pdf = render(spec)
        report = preflight(spec, pdf)
        if report.ok and not report.issues:
            return AgentResult(spec=spec, pdf=pdf, report=report, iterations=i, status="ok")

        fixable = [iss for iss in report.issues if iss.code in FIXABLE_CODES]
        if not fixable:
            # Лишились тільки non-fixable issues (§5) — зупиняємось чесно.
            return AgentResult(spec=spec, pdf=pdf, report=report, iterations=i,
                                status="needs_human")

        # Один issue за ітерацію: найпростіша дисципліна на Фазу 1 —
        # LangGraph зі стейтом і паралельними виправленнями буде у Фазі 2.
        issue = fixable[0]
        spec = FIXABLE_CODES[issue.code](spec, issue)

    return AgentResult(spec=spec, pdf=render(spec), report=preflight(spec, ...),
                        iterations=max_iterations, status="max_iterations_reached")
```

**Умови зупинки (усі три обов'язкові, не тільки "ok"):**
1. `ok=True` і `issues == []` — успіх.
2. Залишились тільки issues поза `FIXABLE_CODES` — статус `needs_human`,
   агент НЕ намагається щось вигадати понад свої 3 інструменти.
3. `max_iterations` вичерпано — статус `max_iterations_reached` (запобіжник
   від зациклення, напр. якщо два fix-и конфліктують і issue повертається).

`FIXABLE_CODES = {"bleed_too_small": fix_bleed, "out_of_media": clamp_to_media,
"low_dpi": shrink_photo_zone}` — явний allowlist, не «спробувати щось» для
невідомих кодів.

## 5. Non-fixable issues — чесна поведінка, не вдавана

| Код | Чому агент Фази 1 його НЕ виправляє |
|---|---|
| `rgb_in_print` | Симптом збою в `render()`/`cmyk.py`, не проблема конкретного дизайну — редагування Canvas JSON тут безсиле. Сигнал розробнику, не клієнту. |
| `no_pdfx_intent` | Конфігураційна проблема (`PRINT_ICC_PROFILE` не заданий) — рівень оточення, не рівень дизайну. |
| `not_pdf` | Те саме — збій pipeline, не дизайну. |
| `image_unreadable` | `info`-рівень, не блокує `ok`; агент міг би скипнути DPI-перевірку для цього елемента, але не редагує спек наосліп без відомого розміру растра. |

Це навмисно вузький agentic scope: **Фаза 1 виправляє проблеми дизайну,
не проблеми інфраструктури.** Розширювати `FIXABLE_CODES` на інфраструктурні
коди — антипатерн (агент почне «лікувати» симптоми збою pipeline, ховаючи
реальний баг).

## 6. Приклад проходу (для тестів)

```
Вхід: CanvasJSON з bleed_mm=1 (< 3) і image element за межами медіабоксу.

Ітерація 0: preflight -> issues=[bleed_too_small, out_of_media]
            -> fix_bleed(spec, 3.0)
Ітерація 1: preflight -> issues=[out_of_media]  (bleed вже ок)
            -> clamp_to_media(spec, idx=0)
Ітерація 2: preflight -> issues=[]  -> status="ok", iterations=2
```

## 7. Acceptance criteria

- [ ] `preflight_agent()` на прикладі з розділу 6 сходиться за ≤3 ітерації
      до `status="ok"`.
- [ ] Синтетичний CanvasJSON тільки з `rgb_in_print` (примусово зіпсований
      PDF у тесті) → `status="needs_human"` за 1 ітерацію, спека НЕ мінялась.
- [ ] Синтетичний конфліктний кейс (fix одного issue провокує інший
      нескінченно) → `status="max_iterations_reached"`, не падає й не висить.
- [ ] Кожен fix-інструмент має unit-тест окремо від циклу (детермінований
      вхід/вихід, без preflight/render навколо).
- [ ] `cli.py`: нова підкоманда `python -m server.cli preflight-fix <spec>
      [-o output.json]` — прогонити агента і зберегти виправлений CanvasJSON
      (аналогічно існуючим `render`/`prompt`/`preflight`).

## 7a. Рішення, потрібні від Антона

> [!TODO]
> 1. `element_index` як структуроване поле в `PreflightIssue` (§3.2) — так чи
>    парсити з `message` і далі?
> 2. Overlap-check для `shrink_photo_zone` (§3.3) — Фаза 1 чи пізніше?
> 3. `max_iterations=5` (§4) — ок як дефолт, чи є краще число з практики?

---

## 8. Support-бот на RAG по доці — окремо, не блокує §1–7

STRATEGY.md §3 Фаза 1 згадує «Support-бот на RAG по доке» поряд з
preflight-агентом — концептуально це той самий рівень складності (агент з
інструментом retrieval), але функціонально не залежить від preflight-циклу.

**Мінімальний обсяг для Фази 1** (не чекаючи розділів 1–7):
- Джерело знань: `CLAUDE.md`, `README.md`, `docs/DECISIONS.md`, `docs/specs/*`
  — усе, що вже є в репо, без окремої бази знань.
- Retrieval: простий keyword/embedding пошук по markdown-файлах (без
  векторної БД — це Фаза 1, не інфраструктурний проєкт); Supabase з'являється
  за потреби, а не заздалегідь.
- Один Claude-виклик: питання користувача + top-k знайдених фрагментів →
  відповідь. Без агентного циклу (це просто RAG, не ReAct) — щоб не плутати
  з preflight-агентом вище.

> [!TODO] Антон: пріоритизувати підтримка-бот у цій же гілці
> (`feat/p1-preflight-agent`) чи окрема гілка `feat/p1-support-bot`, паралельно?
> Рекомендація: окрема гілка — вони не ділять код, паралельний review швидший.

---

**Наступний крок після мерджу:** Фаза 2 (`feat/p2-product-graph`) —
перенести цю ж дисципліну (issue → fix → re-check) на LangGraph StateGraph,
де стане можливим паралельне виправлення кількох issues й памʼять між
запусками.
