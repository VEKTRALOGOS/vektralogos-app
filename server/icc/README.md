# ICC-профілі (локально, не в репозиторії)

Друкарський CMYK/PDF-X тракт (`server/cmyk.py`) використовує ICC-профіль для
конверсії RGB→CMYK і як OutputIntent у PDF/X-3. Профіль Фази 0 — **ISO Coated v2
(ECI)**, характеризація **FOGRA39** (`ISOcoated_v2_eci.icc`).

Бінарник тут **не зберігається** (тримаємо репозиторій без важких асетів;
профіль вільно доступний з першоджерела). Завантаж локально:

```bash
make fetch-icc
```

Це качає офіційний пакет ECI (`eci_offset_2009.zip`, ~17 МБ) з
<https://eci.org/> і кладе `ISOcoated_v2_eci.icc` сюди. Далі пропиши шлях у `.env`:

```
PRINT_ICC_PROFILE=/абсолютний/шлях/до/server/icc/ISOcoated_v2_eci.icc
```

Якщо `PRINT_ICC_PROFILE` не заданий — `cmyk.py` усе одно робить CMYK, але не
повноцінний PDF/X (лог попереджає).

**Джерело / ліцензія:** European Color Initiative (ECI), <https://eci.org/> —
профілі вільно розповсюджуються для друкарського використання. Пізніші фази
можуть додати сучасні профілі (PSO Coated v3) чи SWOP для US-ринку.
