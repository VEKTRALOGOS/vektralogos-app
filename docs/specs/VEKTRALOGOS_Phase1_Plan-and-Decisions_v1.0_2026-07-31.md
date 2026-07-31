# Vektralogos — Фаза 1: план реалізації + відповіді на зауваження

**Автор:** Claude Code (виконавець)
**Адресат:** Claude AI (стратег) / Антон
**Дата:** 2026-07-31
**Відповідає на:** `VEKTRALOGOS_SPEC_Phase1-Preflight-Agent_v1.0_2026-07-31.md`
**Гілка:** `feat/p1-preflight-agent`

> Це не переписування спеки (її автор — Claude AI). Це відповідь виконавця:
> рішення по відкритих `[!TODO]`/§7a, інженерні уточнення, які виявились при
> читанні коду, і фінальний план + схема інструментів перед кодуванням.

---

## A. Відповіді на відкриті питання спеки

### A1. `element_index` як структуроване поле в `PreflightIssue` (§3.2, §7a.1)

**Рішення: ТАК — додати `element_index: int | None = None`.** Збігається з
рекомендацією спеки. Парсинг індексу регексом з `message` — технічний борг з
першого дня: `message` — це людський текст (українською, з форматуванням), а
агент споживає його машинно. Одна зміна `re.sub` у форматуванні повідомлення
ламає агента мовчки.

- Публічний контракт `{ok, issues}` не змінюється по суті — поле лише
  додається (`to_dict` отримує `"element_index"`), нічого не видаляється.
- Заповнюємо `element_index=idx` там, де issue стосується елемента (`low_dpi`,
  `out_of_media`, `image_unreadable`). Для `bleed_too_small`/`rgb_in_print`/
  `no_pdfx_intent`/`not_pdf` лишається `None` (вони не про елемент).
- Мінімальна зміна `preflight.py`, повністю зворотньо сумісна.

### A2. Overlap-check для `shrink_photo_zone` (§3.3, §7a.2)

**Рішення: ПОЗА Фазою 1.** Collision/composition — окремий рівень
відповідальності, якому потрібна модель розкладки; це природно лягає на
Фазу 2 (LangGraph тримає стан композиції). Аргумент, чому це безпечно
відкласти саме для `shrink_photo_zone`:

- Інструмент **зменшує** зону й **зберігає якір `x_mm,y_mm`** (§3.3), тобто
  усадка йде до верхнього-лівого кута. Він не створює НОВИХ накладань — у
  гіршому разі лишає візуальну «дірку», але ніколи не жене піксель на друк
  нижче 300 DPI.
- Задокументуємо це чесно в докстрінгу інструмента як відомий ліміт Фази 1.

Ревізія в Фазі 2 (`feat/p2-product-graph`): overlap як окрема перевірка +
можливе виправлення в графі.

### A3. `max_iterations=5` як дефолт (§7a.3)

**Рішення: сирий лічильник — не головна умова зупинки; додаємо детекцію
відсутності прогресу.** Проблема сирого числа: за дисципліни «один issue за
ітерацію» N незалежних issue потребують N ітерацій. Приклад із 4 елементами
`out_of_media` + bleed = 5 фіксів — впритул до дефолту, хоча все виправно й
без конфліктів. Це хибний `max_iterations_reached`.

Тому:

- **Первинна умова зупинки — фікспойнт/непрогрес:** порівнюємо *підпис*
  набору issue (відсортований мультимножина `(code, element_index)`) до і
  після фіксу. Якщо після застосування фіксу підпис не «зменшився» (той самий
  або більший) — це конфлікт → стоп зі статусом `no_progress`. Це точно
  реалізує acceptance #3 (конфліктний кейс), не покладаючись на вгадане число.
- **`max_iterations` лишається жорстким запобіжником**, але масштабованим:
  дефолт `max(8, 3 * len(fixable_на_старті))`. Параметр відкритий для тестів
  (конфліктний кейс форсимо малим `max_iterations`).

### A4. Support-бот: та сама гілка чи окрема (§8)

**Рішення: окрема гілка `feat/p1-support-bot`, ПІСЛЯ мержу preflight-агента
(не строго паралельно).** Погоджуюсь зі спекою, що код не спільний і review
швидший окремо. Уточнення для соло-режиму: не вести дві гілки одночасно —
спершу довести preflight-агент до мержу (це стрижень Фази 1), далі support-бот
окремим маленьким PR. Так кожен PR лишається малим і оглядовим.

---

## B. Інженерні уточнення (виявлено при читанні коду — не було в спеці)

### B1. Не рендерити PDF на КОЖНІЙ ітерації

Псевдокод §4 викликає `render(spec)` щоітерації. Але всі три `FIXABLE_CODES`
(`bleed_too_small`, `out_of_media`, `low_dpi`) — перевірки **рівня спеки**, їм
PDF не потрібен. `render()` тягне Ghostscript: повільно, може впасти, вимагає
gs у середовищі. Ключове: `preflight(spec, pdf=None)` вже виконує ЛИШЕ
spec-level перевірки (див. `preflight.py` — блок `if pdf is not None` просто
пропускається).

**План петлі:**
1. Цикл фіксів гоняє `preflight(spec)` **без PDF** — дешево, без gs,
   детерміновано.
2. Коли spec-level issue вичерпані — **рендеримо один раз**, `preflight(spec,
   pdf)` для фінального звіту (сюди потрапляють `rgb_in_print`/`no_pdfx_intent`).

Виграш: петля тестується без Ghostscript (acceptance #1/#3 — чисто на спеці),
на порядок швидша, і чесно віддзеркалює, що fix-и не залежать від рендеру.

### B2. Адаптер `issue → args` (сигнатури не збігаються з dispatch)

У спеці fix-функції мають різні сигнатури (`fix_bleed(spec, min_bleed_mm)`,
`clamp_to_media(spec, element_index)`, `shrink_photo_zone(spec, element_index,
min_dpi)`), а dispatch у §4 кличе однаково: `FIXABLE_CODES[code](spec, issue)`.
Це не зійдеться напряму.

**План:** тримаємо ДВА рівні:
- **Чисті fix-функції** — точні сигнатури зі спеки, юніт-тестуються окремо
  (acceptance #4).
- **Тонкі адаптери** `(spec, issue) -> spec` у `FIXABLE_CODES` — дістають
  `issue.element_index` (див. A1) і кличуть чисту функцію. Уся «розпаковка
  issue» живе тут, а не в бізнес-логіці фіксу.

### B3. Незмінність

Спека називає фікси «чистими функціями без побічних ефектів». Реалізуємо
буквально: кожен fix повертає **новий** `CanvasJSON` через
`spec.model_copy(deep=True)` з модифікацією, не мутуючи вхід. Так цикл і тести
не ловлять прихованих aliasing-багів.

### B4. Семантика статусів — не плутати нешкідливий `warn` з `error`

§4 має ризик: якщо ICC не заданий, лишається `no_pdfx_intent` (**warn**,
проблема оточення, не дизайну) — і `issues != []` назавжди. За буквальним
§4 це дало б `needs_human` на рівному місці.

**Уточнена семантика (сумісна з §5 «чесна поведінка»):**
- `ok` — немає `error` І не лишилось виправних issue. Дизайн придатний до
  друку. Нешкідливий `no_pdfx_intent` (config-warn) статус НЕ псує — це нота
  оточення, повертаємо її в звіті, але не блокуємо.
- `needs_human` — лишився **error**, який агент не чинить (`rgb_in_print`,
  `not_pdf`): це збій pipeline/оточення → ескалація розробнику (точно §5).
- `no_progress` — виправний issue повертається після фіксу (конфлікт).
- `max_iterations_reached` — жорсткий запобіжник спрацював.

Acceptance #2 (`rgb_in_print` → `needs_human`) і #1 (сходиться до `ok`) —
обидва тримаються.

### B5. Чесність слова «агент» (нема LLM у петлі — і це правильно)

У preflight-петлі **немає виклику LLM**: вибір інструмента — детермінований
1:1 маппінг `code → fix`. Це відповідає STRATEGY §3 «agent loop / tool use»
за структурою (петля ReAct: observe→act→observe), але без генеративного
кроку — і для коректності друку це РИСА, не хиба: друкарський фікс не можна
віддавати на «вгадування» моделі. Генеративний/ReAct-досвід з LLM свідомо
живе в support-боті (RAG, §8) і в наступних фазах. Це варто написати прямо,
щоб «агент» не звучав як маркетинг.

---

## C. Схема інструментів агента

| Інструмент (чистий) | Код issue → тригер | Рівень | Сигнатура | Що робить | Детермінізм |
|---|---|---|---|---|---|
| `fix_bleed` | `bleed_too_small` | spec | `(spec, min_bleed_mm=3.0) -> spec` | `canvas.bleed_mm = max(поточний, min)` | усуває за 1 прохід |
| `clamp_to_media` | `out_of_media` | spec | `(spec, element_index, ...) -> spec` | зсуває елемент у `[-bleed..w+bleed]×[-bleed..h+bleed]`, зберігає w/h; текст — як точку-якір | усуває за 1 прохід |
| `shrink_photo_zone` | `low_dpi` | spec | `(spec, element_index, min_dpi=300) -> spec` | зменшує `width_mm/height_mm` фото-зони (aspect ratio, якір лишається) до DPI≥min; файл НЕ чіпає | усуває за 1 прохід |

**Allowlist:** `FIXABLE_CODES = {"bleed_too_small", "out_of_media", "low_dpi"}`
(через адаптери B2). Явний список — не «спробувати щось» на невідомий код.

**Не-виправні (§5), поведінка — чесна ескалація, не імітація:**
`rgb_in_print`, `no_pdfx_intent`, `not_pdf`, `image_unreadable` — агент їх НЕ
редагує (симптоми збою pipeline/оточення, не дизайну).

---

## D. Архітектура петлі (оновлений псевдокод)

```python
def preflight_agent(spec, *, min_dpi=300, min_bleed_mm=3.0,
                    max_iterations=None) -> AgentResult:
    start = preflight(spec, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
    fixable0 = [i for i in start.issues if i.code in FIXABLE_CODES]
    if max_iterations is None:
        max_iterations = max(8, 3 * len(fixable0))

    prev_sig = None
    for i in range(max_iterations):
        report = preflight(spec, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)  # без PDF (B1)
        fixable = [iss for iss in report.issues if iss.code in FIXABLE_CODES]

        if not fixable:                       # spec-level чисто → фінальний рендер (B1)
            return _finalize(spec, report, i, min_dpi, min_bleed_mm)

        sig = sorted((iss.code, iss.element_index) for iss in fixable)
        if sig == prev_sig:                   # фікс не зрушив набір → конфлікт (A3)
            return _finalize(spec, report, i, min_dpi, min_bleed_mm,
                             forced="no_progress")
        prev_sig = sig

        issue = fixable[0]                    # один issue за ітерацію (дисципліна §4)
        spec = FIXABLE_CODES[issue.code](spec, issue)   # адаптер (B2), новий spec (B3)

    return _finalize(spec, preflight(spec, ...), max_iterations, ...,
                     forced="max_iterations_reached")


def _finalize(spec, spec_report, iters, min_dpi, min_bleed_mm, forced=None):
    pdf = render(spec)                                   # рендер ОДИН раз (B1)
    final = preflight(spec, pdf, min_dpi=min_dpi, min_bleed_mm=min_bleed_mm)
    status = forced or _classify(final)                 # ok / needs_human (B4)
    return AgentResult(spec=spec, pdf=pdf, report=final, iterations=iters, status=status)
```

`_classify`: є `error` → `needs_human`; інакше `ok` (нешкідливий
`no_pdfx_intent`-warn не блокує, B4).

---

## E. Обсяг робіт (файли, тести, CLI)

**Код**
- `server/preflight.py` — додати `element_index: int | None = None` у
  `PreflightIssue` + заповнення + `to_dict` (A1). Більше нічого не чіпаємо.
- `server/preflight_agent.py` (новий) — `AgentResult`, чисті fix-функції,
  адаптери, `FIXABLE_CODES`, `preflight_agent()`.
- `server/__init__.py` — експорт `preflight_agent`, `AgentResult`.
- `server/cli.py` — підкоманда `preflight-fix <spec> [-o out.json]` (acceptance #5).

**Тести** (`tests/test_preflight_agent.py`)
- Кожен fix окремо, детермінований in/out (acceptance #4).
- Приклад §6: `bleed=1` + елемент за медіабоксом → `ok` за ≤3 ітерації (#1).
- `rgb_in_print` (форсимо зіпсований PDF) → `needs_human`, spec не мінявся (#2).
- Конфліктний кейс → `no_progress`/`max_iterations_reached`, не падає (#3).

**Support-бот (§8)** — окрема гілка `feat/p1-support-bot` після мержу (A4).

---

## F. Що потрібно від Антона

1. **A2 overlap-check** — підтвердити «поза Фазою 1» (моя рекомендація) чи
   потрібен вже зараз.
2. **A4 support-бот** — окрема гілка після мержу preflight-агента (рекоменд.)
   чи інакше.
3. Решта (A1 `element_index`=так, A3 непрогрес+масштабований cap, B1–B5) —
   інженерні дефолти, беру на себе, якщо не заперечиш.
