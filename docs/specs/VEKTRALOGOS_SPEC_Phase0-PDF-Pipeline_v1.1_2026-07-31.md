# Vektralogos — Спека Фази 0: PDF-Pipeline (v1.1)

**Гілка:** `feat/p0-pdf-pipeline`
**Автор спеки:** Claude AI (стратег/промпт-інженер)
**Виконавець:** Claude Code
**Дата:** 2026-07-31
**Статус:** v1.1 — приведено у відповідність з реалізацією
(`server/schema.py`, `server/brief.py`, `server/templater.py`,
`server/prompt_to_canvas.py`). Замінює v1.0.

> [!NOTE]
> Три архітектурні рішення прийняті разом з Claude Code і вже зафіксовані
> в `docs/DECISIONS.md`: (1) гібрид `prompt → DesignBrief → CanvasJSON`,
> (2) `server/schema.py` — канонічна CanvasJSON-схема, (3) preflight/FOGRA39
> відкладені у кінець фази. Ця версія спеки лише документує їх, рішення не
> змінює.

---

## 1. Мета фази

Без змін від v1.0: **Canvas JSON → друкарський PDF** (CMYK, вектор, кирилиця
в кривих), плюс **осмислений** end-to-end шлях `текст клієнта → PDF`.
Зміна проти v1.0: цей шлях — не один LLM-виклик, а **гібрид** (§5).

## 2. Скоуп

Без змін від v1.0, крім одного уточнення:
- ~~Один Claude Opus 5 виклик: промпт → JSON-опис дизайну~~ →
  **Claude Opus 5 виклик (промпт → DesignBrief) + детермінований
  шаблонизатор (DesignBrief → Canvas JSON).** LLM відповідає за смисл,
  шаблонизатор — за міліметри.

## 3. Архітектура тракту (v1.1)

```
Клієнт: prompt (текст) ──> [claude-opus-5: prompt_to_brief] ──> DesignBrief
                                                                    │
                                              [templater: brief_to_canvas]
                                                                    ▼
                                                              CanvasJSON
                                                                    │
                              fonttools (text → outlines, Noto Sans)
                                                                    ▼
                                              vector PDF (ReportLab, без SVG)
                                                                    ▼
                                        Ghostscript (CMYK + ICC FOGRA39/SWOP)
                                                                    ▼
                                              Preflight {ok, issues} (кінець фази)
```

**Зміна проти v1.0/CLAUDE.md §3:** проміжного SVG-файлу в серверному тракті
немає. `JSON → fonttools outlines → vector PDF (ReportLab) → Ghostscript`.
SVG лишається тільки як клієнтське прев'ю (Fabric.js), pipeline його не чіпає
— знімає ризик, що SVG→PDF-конвертер розтеризує кирилицю. (DECISIONS.md,
«Print-тракт без SVG-файлу».)

## 4. Canvas JSON — канонічна схема (`server/schema.py`)

Надмножина чернетки з v1.0. Джерело правди — Pydantic-модель у коді, тут —
довідкова копія для спек/промптів.

```json
{
  "version": "1.0",
  "canvas": {
    "width_mm": 90, "height_mm": 50, "bleed_mm": 3, "dpi": 300,
    "background": { "rgb": "#FFFFFF" }
  },
  "fonts": [{ "family": "Noto Sans", "file": "NotoSans-Regular.ttf" }],
  "elements": [
    { "type": "rect", "x_mm": 0, "y_mm": 0, "width_mm": 90, "height_mm": 8,
      "fill": { "cmyk": [0.0, 0.8, 1.0, 0.0] }, "rotation_deg": 0 },
    { "type": "text", "x_mm": 6, "y_mm": 26, "text": "Привіт, світ!",
      "font": "Noto Sans", "size_pt": 22, "fill": { "rgb": "#111111" },
      "align": "left" },
    { "type": "image", "x_mm": 6, "y_mm": 6, "width_mm": 20, "height_mm": 20,
      "src": "photo.jpg", "is_photo_zone": true }
  ]
}
```

Ключові відмінності від чернетки v1.0:

| Поле | v1.0 (чернетка) | v1.1 (код) |
|---|---|---|
| Колір | тільки hex RGB | `{"rgb": "#RRGGBB"}` **або** `{"cmyk": [c,m,y,k]}` (0..1) |
| Шрифти | не було реєстру | `fonts: [{family, file}]` — обов'язковий реєстр, `CanvasJSON.font_file()` кидає помилку з контекстом, якщо `family` не оголошено |
| Елементи | лише `text`, `photo_zone` | `text`, `rect`, `image` (discriminated union по `type`) |
| Растр | `photo_zone` як окремий тип | `image` з обов'язковим `is_photo_zone: true` (guardrail у типі — не можна створити image без цього прапорця) |
| `canvas` | width/height/bleed | + `dpi` (default 300), `background: Color \| null` |
| `extra` | не заявлено | `extra="forbid"` на всіх моделях — зайві поля від LLM/клієнта відхиляються на валідації |

> [!IMPORTANT]
> `is_photo_zone: Literal[True]` у `ImageElement` — це guardrail-інваріант
> §4 CLAUDE.md, зафіксований у типі, а не в рантайм-перевірці. Тест
> `tests/test_invariants.py` мусить це покривати.

## 5. Гібридний LLM-контракт: DesignBrief → шаблонизатор → CanvasJSON

### 5.1 Крок 1 — `prompt_to_brief` (`server/brief.py`)

Без змін по суті від v1.0 §5, з двома уточненнями з практики:
- **Легкий шорткат:** `len(prompt.strip()) < 3` → одразу
  `neutral_default_brief()`, без виклику API. Це реалізація "Варіант B-lite"
  з `VEKTRALOGOS_PROMPTS_Phase0-EdgeCases`.
- **System prompt** — фінальна версія "Варіант A" з edge-cases документа
  (бренди / порожній ввід / невідомі дані) вже в коді як константа `SYSTEM`.
  Спека і код тепер синхронні — окремо оновлювати edge-cases файл не треба.
- Виклик через `client.messages.parse(..., output_format=DesignBrief)` —
  структурований вивід типізований напряму в Pydantic-модель, без ручного
  парсингу JSON.
- `response.stop_reason == "refusal"` — окрема гілка обробки (модель
  відмовилась генерувати бриф) — цього не було в чернетці v1.0.

### 5.2 DesignBrief schema (без змін від v1.0 §5.3, підтверджено кодом)

```python
class BriefTextElement:
    content: str
    role: Literal["name", "title", "message", "date"]
    assumed: bool = False

class DesignBrief:
    style: str
    palette: list[str]  # 1..3 hex-кольори
    text_elements: list[BriefTextElement]
    layout_hint: Literal["centered", "left-aligned", "banner", "corner"]
```

### 5.3 Крок 2 — `brief_to_canvas` (`server/templater.py`) — НОВЕ проти v1.0

Детермінований, без LLM. Політика Фази 0 (проста навмисно):
- Порядок і кегль елементів — за роллю: `title 16pt → name 28pt →
  message 13pt → date 11pt` (стек зверху вниз).
- `layout_hint == "centered"` → вертикальне центрування блоку і
  горизонтальне center; інші hints → від верхнього поля, left-align.
- Колір тексту — найтемніший колір з `palette` (fallback `#1A1A1A`, якщо
  вся палітра світла — читабельність понад точність кольору).
- Фон завжди білий (`#FFFFFF`) — безпечний дефолт для друку.
- Порожній `content.strip()` — елемент пропускається (не потрапляє в
  CanvasJSON).

> [!NOTE]
> Це відповідає на відкрите питання v1.0 «чи `text_elements: []` — ок»:
> так, шаблонизатор коректно обробляє порожній список — просто не додає
> текстових елементів на полотно (буде тільки фон).

### 5.4 Розміри полотна (`server/prompt_to_canvas.py`) — НОВЕ проти v1.0

```python
PAPER_SIZES = {
    "a4": (210.0, 297.0), "a5": (148.0, 210.0),
    "a6": (105.0, 148.0), "card": (90.0, 50.0),
}
DEFAULT_PAPER = "a6"
```

Закриває відкрите питання v1.0 §8.1 (розмір за замовчуванням) — **A6**,
як і рекомендувалось.

## 6. Acceptance criteria — статус (усі закриті)

- [x] ✅ Текст у вихідному PDF справді в кривих (fonttools outlines; тест:
      немає операторів показу тексту / немає `/FontFile`).
- [x] ✅ Вихідний PDF справді CMYK + ICC — **зроблено**. FOGRA39
      (`ISOcoated_v2_eci.icc`, ECI) підключено, `render()` дає PDF/X-3 з
      OutputIntent (`make fetch-icc`, `PRINT_ICC_PROFILE`).
- [x] ✅ Кирилиця (ї/є/ґ/і) коректна — «Ґудзик, їжак, єнот» рендериться;
      живий прогон #5 зберіг апостроф у «Кав'ярня Їжачок».
- [x] ✅ `image` без `is_photo_zone: true` — CanvasJSON не валідується
      (`tests/test_invariants.py`).
- [x] ✅ `prompt_to_brief` на 10 промптах — **прогнано вживу (claude-opus-5)**:
      усі 10 дали валідний DesignBrief, жодного краху. Критичні: #6 бренд
      відсічено (порожній `text_elements`), #9 невідома дата не вигадана
      (`content=""`, `assumed=true`), #10 name/title розділено без зміни
      регістру. Гарнесс: `scripts/run_test_prompts.py`.
- [x] ✅ `brief_to_canvas` — валідний CanvasJSON, рендериться без винятків
      (`tests/test_brief_templater.py` + живий прогон).
- [x] ✅ Невідомий `font` у `TextElement` — `CanvasJSON.font_file()` кидає
      `KeyError` з переліком шрифтів (`tests/test_invariants.py`).

**Додатково зроблено:** preflight-функція `{ok, issues}` (`server/preflight.py`,
CLI `preflight`) — bleed / DPI фото-зон / RGB-у-друку / PDF-X intent / межі.
Тестів усього: 22 passed.

> Фаза 0 повністю закрита за критеріями. Гілка `feat/p0-pdf-pipeline` готова.

## 7. Рішення — закриті (архів v1.0 §8)

| # | Питання з v1.0 | Рішення | Де зафіксовано |
|---|---|---|---|
| 1 | LLM → CanvasJSON напряму чи гібрид? | Гібрид `prompt → DesignBrief → CanvasJSON` | DECISIONS.md |
| 2 | Чия схема канонічна — спека чи код? | `server/schema.py` (код), спека наздоганяє | DECISIONS.md |
| 3 | Preflight/FOGRA39 зараз чи в кінці фази? | Кінець фази | DECISIONS.md |
| 4 (v1.0 §8.1) | Розмір паперу за замовчуванням | A6 | `prompt_to_canvas.py` |
| 5 (v1.0 §8.2) | Node-фронт одразу чи Python-only спочатку | Python-only (client/ поки порожній) | структура репо |

Відкритих питань до Антона на цю мить немає — Фаза 0 в реалізації, наступний
чекпойнт: preflight-функція + FOGRA39 ICC перед мерджем (§6, п.2).
