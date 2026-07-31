# Vektralogos — Спека Фази 0: PDF-Pipeline

**Гілка:** `feat/p0-pdf-pipeline`
**Автор спеки:** Claude AI (стратег/промпт-інженер)
**Виконавець:** Claude Code
**Дата:** 2026-07-31
**Статус:** draft → на ревʼю Антону

---

## 1. Мета фази

Один прохід: **Canvas JSON → друкарський PDF (CMYK, вектор, кирилиця в кривих)**
+ один Claude-виклик **"промпт клієнта → структурований опис дизайну"**.

Це субстрат: агентів ще немає (Фаза 1), є один LLM-виклик зі structured output
і один детермінований pipeline. Ціль фази — довести, що ров (коректний
друкарський вектор) реально працює end-to-end.

## 2. Скоуп

**В скоупі:**
- Каркас Shopify-app (Node/TS фронт, Python бекенд — окремі процеси/сервіси).
- Fabric.js: canvas → export Canvas JSON (тільки прев'ю в браузері, resvg-рендер
  для мініатюр — НЕ для print).
- Python pipeline: Canvas JSON → SVG → text-to-outlines (fonttools) → vector PDF
  → Ghostscript (CMYK, ICC FOGRA39, PDF/X).
- Один Claude Opus 5 виклик: вільний текстовий промпт клієнта → JSON-опис
  дизайну (structured output, `output_config.format`).
- Мінімальний preflight як функція (не агент): перевірка DPI/bleed/CMYK на
  виході — просто assert/repor, без циклу виправлень (це Фаза 1).

**Поза скоупом (навмисно):**
- Агентний цикл виправлення помилок (Фаза 1).
- LangGraph, стейт, ветвлення (Фаза 2+).
- Shopify Billing, App Store лістинг.
- Растрова генеративка/автовекторизація зображень.

## 3. Тверді інваріанти (з CLAUDE.md, не порушувати)

| # | Інваріант |
|---|---|
| 1 | Canvas JSON — єдине джерело правди; клієнт і сервер парсять один і той самий JSON тими самими `.ttf`. |
| 2 | Друкарський файл = вектор. `resvg` тільки для прев'ю-мініатюр. |
| 3 | Текст → криві (outlines) за замовчуванням при складанні PDF. |
| 4 | CMYK/PDF-X — тільки через Ghostscript + ICC (FOGRA39/SWOP), не native CMYK бібліотек. |
| 5 | Растрові зображення — лише в явну «фото-зону», без авто-векторизації лайн-арту. |

## 4. Canvas JSON — схема Фази 0

> [!NOTE]
> **Реалізовано (Claude Code, 2026-07-31).** Канонічна схема — Pydantic-модель
> `server/schema.py` (надмножина чернетки: реєстр шрифтів, RGB/CMYK, `rect`,
> `dpi`, `background`). Приклад нижче приведено у відповідність із кодом.

```json
{
  "version": "1.0",
  "canvas": {
    "width_mm": 105, "height_mm": 148,
    "bleed_mm": 3, "dpi": 300,
    "background": { "rgb": "#FFFFFF" }
  },
  "fonts": [
    { "family": "Noto Sans", "file": "NotoSans-Regular.ttf" }
  ],
  "elements": [
    {
      "type": "text",
      "text": "Іван Петренко",
      "font": "Noto Sans", "size_pt": 24,
      "x_mm": 20, "y_mm": 40,
      "fill": { "rgb": "#1A1A1A" },
      "align": "left"
    },
    {
      "type": "rect",
      "x_mm": 0, "y_mm": 0, "width_mm": 105, "height_mm": 8,
      "fill": { "cmyk": [0, 0.8, 1, 0] }
    },
    {
      "type": "image",
      "x_mm": 10, "y_mm": 10, "width_mm": 50, "height_mm": 50,
      "src": "https://...", "is_photo_zone": true
    }
  ]
}
```

Ключові відмінності від чернетки: `content`→`text`, `font_family`→`font`
(з реєстру `fonts[]`), `font_size_pt`→`size_pt`, `color_hex`→`fill` (RGB **або**
CMYK), `photo_zone`→`image` з обов'язковим `is_photo_zone:true`, `w_mm/h_mm`→
`width_mm/height_mm`, `asset_url`→`src`.

> [!IMPORTANT]
> `image` (з `is_photo_zone:true`) — єдиний тип, що дозволяє растр. Схема
> відхиляє `image` без цього прапорця. Текст/фігури лишаються вектором по
> всьому тракту.

## 5. Claude Opus 5 — виклик "промпт → JSON опис дизайну"

### 5.1 Призначення
Клієнт магазину пише вільним текстом, що хоче ("листівка з іменем Іван,
синьо-золота, святкова"). Виклик перетворює це на структурований
`DesignBrief`, який далі використовує генератор/шаблонизатор (не в скоупі
Фази 0 — тут лише сам виклик і схема).

### 5.2 System prompt (чернетка для Claude Code — вставити як є)

```
Ти — асистент дизайну для персоналізації друкованих товарів (листівки,
гравіювання, мерч). Клієнт магазину описує, що хоче, вільним текстом
(можливо українською, російською або англійською).

Твоя задача: перетворити опис на структурований DesignBrief.

Правила:
- Якщо клієнт не вказав колір/стиль — заповни розумним дефолтом, познач
  "assumed": true для цього поля.
- Текстові поля (імена, підписи) винось у "text_elements" окремо — це
  реальний вміст для друку, не вигадуй і не змінюй орфографію імені клієнта.
- Ніколи не додавай товарні знаки, бренди, символіку третіх осіб.
- Відповідай ЛИШЕ валідним JSON за схемою нижче. Без пояснень, без markdown.
```

### 5.3 Output schema (structured output / `output_config.format`)

```json
{
  "type": "object",
  "required": ["style", "palette", "text_elements", "layout_hint"],
  "properties": {
    "style": { "type": "string", "description": "e.g. festive, minimal, formal" },
    "palette": {
      "type": "array",
      "items": { "type": "string", "pattern": "^#[0-9A-Fa-f]{6}$" },
      "minItems": 1, "maxItems": 3
    },
    "text_elements": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["content", "role"],
        "properties": {
          "content": { "type": "string" },
          "role": { "type": "string", "enum": ["name", "title", "message", "date"] },
          "assumed": { "type": "boolean" }
        }
      }
    },
    "layout_hint": { "type": "string", "enum": ["centered", "left-aligned", "banner", "corner"] }
  }
}
```

### 5.4 Модель
`claude-opus-5` (дефолт проєкту, DECISIONS.md).

### 5.5 Реалізація — гібридний контракт (Claude Code, 2026-07-31)

> [!NOTE]
> Щоб зберегти end-to-end `prompt → PDF` (ціль §1) і водночас не змушувати
> LLM рахувати міліметрові координати, крок «бриф → раскладка» (у чернетці
> «поза скоупом») реалізовано як **мінімальний детермінований шаблонизатор**:
>
> `prompt --LLM--> DesignBrief (§5.3) --templater--> Canvas JSON (§4) --render--> PDF`
>
> - LLM-крок: `server/brief.py` (`prompt_to_brief`, structured output,
>   system prompt = варіант A з EdgeCases: бренди/порожнє/невідоме; шорткат
>   `len<3` без виклику API).
> - Шаблонизатор: `server/templater.py` (`brief_to_canvas`) — кегль за роллю,
>   вирівнювання за `layout_hint`, колір тексту з палітри, фон білий.
> - Разом: `server/prompt_to_canvas.py` + CLI `prompt` (`--size a4/a5/a6/card`,
>   дефолт a6). Схема `DesignBrief` §5.3 лишилась як є.

## 6. Python pipeline — high-level кроки

> Код не тут (зона Claude Code) — фіксую лише контракт між кроками.

1. **Input:** Canvas JSON (§4) + `bleed_mm`.
2. **JSON → SVG:** детерміноване, 1:1 маппінг елементів → SVG-вузли; шрифти —
   ті самі `.ttf`, що на клієнті.
3. **Text → outlines:** fonttools конвертує весь текст у криві.
4. **SVG → vector PDF:** без растеризації.
5. **Ghostscript:** CMYK-конверсія + ICC (FOGRA39) + PDF/X compliance.
6. **Preflight-функція (не агент):** перевірити DPI фото-зон, bleed, CMYK-профіль
   у вихідному PDF → повернути звіт `{ ok: bool, issues: [...] }`. Без
   автовиправлення — це Фаза 1.

> [!NOTE]
> **Реалізація (Claude Code):** крок 2–4 об'єднано — тракт іде напряму
> `JSON → text-to-outlines (fonttools BasePen) → vector PDF (ReportLab)`, БЕЗ
> проміжного SVG-файлу. Це прибирає ризик, що SVG→PDF-конвертер розтеризує або
> зіпсує кирилицю; SVG лишається лише клієнтським прев'ю-форматом (Fabric.js).
> Крок 6 (preflight) — відкладено (див. §7).

## 7. Acceptance criteria (тести на кожен інваріант, CLAUDE.md §5)

Статус (Claude Code, 2026-07-31): ✅ виконано · 🟡 частково · ⏳ відкладено.

- [x] ✅ Текст у вихідному PDF справді в кривих (тест: немає операторів показу
      тексту `Tj/TJ`, немає `/FontFile`; гліфи — заливки контурів). Візуально
      підтверджено (ґ/ї/є).
- [ ] 🟡 Вихідний PDF справді CMYK **+ FOGRA39 ICC**. CMYK — ✅ (тест на
      оператори `k`, без `rg`). FOGRA39 ICC — ⏳ відкладено (потрібен .icc;
      `cmyk.py` уже підтримує через `PRINT_ICC_PROFILE`).
- [x] ✅ Кирилиця (укр. імена з ї, є, ґ) рендериться коректно (візуальна
      перевірка + повне покриття Noto Sans).
- [x] ✅ Растр лише в `image`/фото-зоні; схема відхиляє `image` без
      `is_photo_zone:true` (тести). Текст растеризувати неможливо — немає
      такого шляху в тракті.
- [ ] ⏳ Claude-виклик на 10 тестових промптах — відкладено (потрібен
      `ANTHROPIC_API_KEY`). Промпти оновити під `DesignBrief` §5.3.

**Відкладено в кінець фази (рішення Антона, вар. 3):** preflight-функція
(§6 крок 6) і FOGRA39 ICC.

## 8. Рішення, потрібні від Антона

> [!TODO] Підтвердити перед стартом
> 1. Розмір паперу за замовчуванням для тестового прогону (A6? A5?).
> 2. Чи потрібен окремий процес для Node-фронту вже у Фазі 0, чи стартуємо
>    Python-only і Shopify-каркас додаємо в кінці фази.
> 3. Тестові промпти для §5.4 — писати мені (Claude AI) чи Антон дає свої.

---

**Наступний крок після мерджу:** Фаза 1 (`feat/p1-preflight-agent`) —
перетворити preflight-функцію з кроку 6 на агента з ReAct-циклом
(`check_dpi`, `check_cmyk`, `vectorize`, `embed_fonts`).
