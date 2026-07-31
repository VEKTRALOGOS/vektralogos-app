# Фікстур: 1★-відгуки конкурентів (синтетичний)

> Синтетичний фікстур для `ingest_feedback` (Фаза 2b, спека §2). Формат — один
> до одного з тим, що очікується від Gemini у `docs/research/` (STRATEGY §5:
> валідація частоти скарги «low-res замість вектора»). Коли Gemini здасть
> реальний ресёрч у цьому ж форматі — парсер не міняється, лише вміст файлу.
>
> Цитати — переказ своїми словами (≤15 слів), не дослівні, щоб уникати
> копірайт-ризику навіть у фікстурі (привчаємо парсер одразу до paraphrase).

## Review: Customily, 1★, 2026-02-14
> Експорт віддає мильний PNG замість вектора — друкарня файл не прийняла.
tags: low-res-export, vector-missing

## Review: Zepto Apparel, 1★, 2026-02-28
> Кирилиця в іменах розсипається на квадратики при генерації PDF.
tags: cyrillic-broken, fonts-missing

## Review: Customily, 1★, 2026-03-09
> Кольори на друку зовсім інші — здається, файл у RGB, не CMYK.
tags: cmyk-wrong, color-shift

## Review: Printful Personalizer, 1★, 2026-03-15
> Немає вильотів під обріз, по краях біла смуга після різки.
tags: bleed-missing, no-print-marks

## Review: Zepto Apparel, 1★, 2026-03-22
> Фото клієнта вставляється як 72 DPI — на футболці все розмите.
tags: low-res-export, no-300dpi

## Review: Kite Custom, 1★, 2026-04-02
> Текст перетворюється на растр, масштабувати без втрати якості неможливо.
tags: vector-missing, text-rasterized

## Review: Customily, 1★, 2026-04-11
> Завантажений шрифт не вбудовується, друкарня бачить дефолтний Arial.
tags: fonts-missing, font-substitution

## Review: Printful Personalizer, 1★, 2026-04-19
> PDF без OutputIntent — типографія просить «нормальний PDF/X», а не цей.
tags: cmyk-wrong, no-pdfx

## Review: Zepto Apparel, 1★, 2026-04-27
> Знову PNG замість SVG/PDF — доводиться перемальовувати логотип вручну.
tags: low-res-export, vector-missing
