"""Тести graph-версії preflight-агента (Фаза 2а, спека §6 acceptance).

Дзеркалить tests/test_preflight_agent.py (Ф1) — той самий контракт входу/
виходу, ті самі сценарії. Плюс: порівняльний тест з Ф1-функцією і перевірка
checkpoint-серіалізації (MemorySaver), яких у Ф1 не було.
"""

from __future__ import annotations

import struct
import zlib

from langgraph.checkpoint.memory import MemorySaver

from server.preflight_agent import preflight_agent
from server.preflight_graph import build_graph, preflight_agent_graph
from server.render import render_vector_pdf
from server.schema import CanvasJSON

# --- хелпери (ті самі, що у test_preflight_agent.py) --------------------------


def _make_png(path: str, w: int, h: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


def _spec(bleed_mm: float = 3.0, elements: list | None = None) -> CanvasJSON:
    return CanvasJSON.model_validate(
        {
            "version": "1.0",
            "canvas": {"width_mm": 90, "height_mm": 50, "bleed_mm": bleed_mm},
            "fonts": [{"family": "Noto Sans", "file": "NotoSans-Regular.ttf"}],
            "elements": elements or [],
        }
    )


def _fake_pdf(_spec_arg: CanvasJSON) -> bytes:
    return b"%PDF-1.4\n% clean cmyk stub\n%%EOF\n"


def _codes(report) -> set[str]:
    return {i.code for i in report.issues}


# --- ті самі сценарії, що Ф1 (acceptance 2a #1) -------------------------------


def test_graph_converges_to_ok():
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(bleed_mm=1.0, elements=[el])
    result = preflight_agent_graph(spec, renderer=_fake_pdf)
    assert result.status == "ok"
    assert result.iterations <= 3
    assert result.spec.canvas.bleed_mm == 3.0
    r = result.spec.elements[0]
    assert r.x_mm + r.width_mm <= 90 + 3 + 1e-9


def test_graph_rgb_in_print_needs_human():
    path = "examples/hello.json"
    import os

    full = os.path.join(os.path.dirname(__file__), "..", path)
    with open(full, encoding="utf-8") as fh:
        spec = CanvasJSON.model_validate_json(fh.read())
    result = preflight_agent_graph(spec, renderer=render_vector_pdf)
    assert result.status == "needs_human"
    assert "rgb_in_print" in _codes(result.report)
    assert result.spec.model_dump() == spec.model_dump()


def test_graph_unfittable_element_stops_no_progress():
    el = {"type": "rect", "x_mm": 0, "y_mm": 0, "width_mm": 200, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(elements=[el])
    result = preflight_agent_graph(spec, renderer=_fake_pdf)
    assert result.status == "no_progress"
    assert "out_of_media" in _codes(result.report)


def test_graph_max_iterations_guard():
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(bleed_mm=1.0, elements=[el])
    result = preflight_agent_graph(spec, renderer=_fake_pdf, max_iterations=1)
    assert result.status == "max_iterations_reached"
    assert result.iterations == 1


def test_graph_shrink_photo_zone_via_low_dpi(tmp_path):
    img = str(tmp_path / "small.png")
    _make_png(img, 50, 50)
    el = {"type": "image", "x_mm": 5, "y_mm": 5, "width_mm": 25, "height_mm": 25,
          "src": img, "is_photo_zone": True}
    spec = _spec(elements=[el])
    result = preflight_agent_graph(spec, renderer=_fake_pdf)
    assert result.status == "ok"
    e = result.spec.elements[0]
    eff = min(50 / (e.width_mm / 25.4), 50 / (e.height_mm / 25.4))
    assert eff >= 300


# --- еквівалентність з Ф1-функцією (acceptance 2a #2) --------------------------


def test_graph_matches_phase1_function_on_spec6_example():
    """Приклад §6 VEKTRALOGOS_SPEC_Phase1-Preflight-Agent: bleed=1 + елемент за медіабоксом."""
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(bleed_mm=1.0, elements=[el])

    r1 = preflight_agent(spec, renderer=_fake_pdf)
    r2 = preflight_agent_graph(spec, renderer=_fake_pdf)

    assert r1.status == r2.status == "ok"
    assert r1.iterations == r2.iterations == 2  # §6: bleed-фікс, потім clamp
    assert r1.spec.model_dump() == r2.spec.model_dump()
    assert r1.report.to_dict() == r2.report.to_dict()


def test_graph_matches_phase1_function_no_progress():
    el = {"type": "rect", "x_mm": 0, "y_mm": 0, "width_mm": 200, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(elements=[el])

    r1 = preflight_agent(spec, renderer=_fake_pdf)
    r2 = preflight_agent_graph(spec, renderer=_fake_pdf)

    assert r1.status == r2.status == "no_progress"
    assert r1.iterations == r2.iterations
    assert r1.spec.model_dump() == r2.spec.model_dump()


# --- checkpoint-серіалізація (acceptance 2a #3) -------------------------------


def test_checkpoints_are_recorded_after_each_node():
    el = {"type": "rect", "x_mm": 80, "y_mm": 0, "width_mm": 40, "height_mm": 10,
          "fill": {"cmyk": [0, 0, 0, 1]}}
    spec = _spec(bleed_mm=1.0, elements=[el])

    cp = MemorySaver()
    app = build_graph(renderer=_fake_pdf, checkpointer=cp)
    config = {"configurable": {"thread_id": "checkpoint-test"}}
    max_iterations = 8
    init = {
        "spec": spec, "min_dpi": 300, "min_bleed_mm": 3.0,
        "max_iterations": max_iterations, "iterations": 0,
        "prev_signature": None, "report": None,
        "forced_status": None, "result": None,
    }
    out = app.invoke(init, config=config)
    assert out["result"].status == "ok"

    history = list(app.get_state_history(config))
    # >1 checkpoint -> справді серіалізується стан після кожного вузла, не
    # лише один фінальний знімок.
    assert len(history) > 1
    # Кожен checkpoint несе розпізнаваний стейт (спеку можна прочитати назад).
    assert all("spec" in snap.values for snap in history if snap.values)
    # Проміжний checkpoint зафіксував крок ДО фінального результату.
    assert any(snap.values.get("result") is None for snap in history)
