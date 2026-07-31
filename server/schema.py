"""Canvas JSON — єдине джерело правди (guardrail §4 CLAUDE.md).

Одна Pydantic-схема обслуговує ОБИДВА шляхи:
  * валідацію вводу від клієнта (Fabric.js) та серверного тракту;
  * `output_config.format` для structured output моделі claude-opus-5.

Конвенції:
  * одиниці — міліметри (фізичний друкарський розмір);
  * початок координат — верхній лівий кут, x -> праворуч, y -> вниз;
  * rotation_deg — за годинниковою стрілкою навколо верхнього лівого кута елемента;
  * колір — RGB (`{"rgb": "#RRGGBB"}`) або CMYK (`{"cmyk": [c, m, y, k]}`, 0..1);
  * z-order — порядок у списку `elements` (знизу вгору).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# --- Кольори -----------------------------------------------------------------

_HEX_RGB = r"^#[0-9A-Fa-f]{6}$"


class RGBColor(BaseModel):
    """Колір у RGB. Ghostscript перекладе у CMYK через ICC на етапі cmyk.py."""

    model_config = ConfigDict(extra="forbid")
    rgb: str = Field(pattern=_HEX_RGB, description="Hex-колір, напр. #111111")


class CMYKColor(BaseModel):
    """Колір, заданий напряму у CMYK (кожен канал 0..1)."""

    model_config = ConfigDict(extra="forbid")
    cmyk: tuple[float, float, float, float] = Field(
        description="[cyan, magenta, yellow, key], кожен у діапазоні 0..1",
    )


Color = Union[RGBColor, CMYKColor]


# --- Полотно та шрифти -------------------------------------------------------


class Canvas(BaseModel):
    """Фізичні параметри друкарського полотна."""

    model_config = ConfigDict(extra="forbid")
    width_mm: float = Field(gt=0, description="Ширина полотна, мм")
    height_mm: float = Field(gt=0, description="Висота полотна, мм")
    bleed_mm: float = Field(default=3.0, ge=0, description="Виліт (bleed), мм")
    dpi: int = Field(default=300, ge=72, description="DPI для растрових фото-зон")
    background: Color | None = Field(default=None, description="Колір фону або null")


class FontRef(BaseModel):
    """Прив'язка family -> .ttf. Клієнт і сервер ОБОВ'ЯЗКОВО беруть той самий файл."""

    model_config = ConfigDict(extra="forbid")
    family: str = Field(description="Ім'я родини, на яке посилаються текстові елементи")
    file: str = Field(description="Ім'я .ttf у server/fonts/, напр. NotoSans-Regular.ttf")


# --- Елементи ----------------------------------------------------------------


class TextElement(BaseModel):
    """Текст. На етапі складання PDF перетворюється у криві (outlines)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    x_mm: float
    y_mm: float
    text: str
    font: str = Field(description="family з canvas.fonts")
    size_pt: float = Field(gt=0, description="Кегль у пунктах (1pt = 1/72 дюйма)")
    fill: Color
    align: Literal["left", "center", "right"] = "left"
    rotation_deg: float = 0.0
    letter_spacing: float = Field(default=0.0, description="Трекінг, додаткові pt між гліфами")
    line_height: float = Field(default=1.2, gt=0, description="Множник міжрядкового інтервалу")


class RectElement(BaseModel):
    """Прямокутник (заливка + опційна обводка)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["rect"] = "rect"
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    fill: Color | None = None
    stroke: Color | None = None
    stroke_width_mm: float = Field(default=0.0, ge=0)
    rotation_deg: float = 0.0
    corner_radius_mm: float = Field(default=0.0, ge=0)


class ImageElement(BaseModel):
    """Растрове зображення — ЛИШЕ у явній фото-зоні (guardrail §4).

    `is_photo_zone` мусить бути True, інакше валідація відхиляє елемент:
    це не дає авто-векторизувати лайн-арт і тримає растр тільки там, де він дозволений.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["image"] = "image"
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    src: str = Field(description="Шлях до растрового файлу (PNG/JPEG)")
    is_photo_zone: Literal[True] = Field(
        description="Мусить бути true — растр дозволений тільки у фото-зоні",
    )
    rotation_deg: float = 0.0


Element = Annotated[
    Union[TextElement, RectElement, ImageElement],
    Field(discriminator="type"),
]


# --- Корінь ------------------------------------------------------------------


class CanvasJSON(BaseModel):
    """Кореневий документ Canvas JSON (v1)."""

    model_config = ConfigDict(extra="forbid")
    version: Literal["1.0"] = "1.0"
    canvas: Canvas
    fonts: list[FontRef] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)

    def font_file(self, family: str) -> str:
        """Повертає ім'я .ttf для family або кидає KeyError із контекстом."""
        for ref in self.fonts:
            if ref.family == family:
                return ref.file
        raise KeyError(
            f"Шрифт '{family}' не оголошено у canvas.fonts "
            f"(наявні: {[f.family for f in self.fonts]})"
        )
