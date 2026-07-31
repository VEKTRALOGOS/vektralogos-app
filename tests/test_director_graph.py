"""Тести Director-агента, milestone 3a (Фаза 3, спека §4 acceptance).

Гейт `validate_demand` детермінований — тестуємо офлайн. Product-воркер
делегує граф 2b; у тестах інжектимо `product_runner`, щоб не чіпати мережу/gs.
"""

from __future__ import annotations

import json

from server.director_graph import (
    Metrics,
    build_director_graph,
    load_metrics,
    run_director,
    validate_demand,
)

_METRICS = "docs/research/fixtures/metrics.json"
_REVIEWS = "docs/research/fixtures/*.md"


def _fake_product(**kwargs) -> dict:
    return {"status": "ok", "diff_path": "/tmp/x/print-safe-bold.json"}


# --- гейт validate_demand (acceptance: логіка §3) -----------------------------


def test_load_metrics_fixture():
    m = load_metrics(_METRICS)
    assert isinstance(m, Metrics)
    assert m.waitlist_signups == 4
    assert m.installs == 0 and m.mrr_usd == 0  # до лістингу — нулі


def test_validate_demand_signal_from_waitlist_only():
    m = Metrics(date="2026-07-31", waitlist_signups=3)
    assert validate_demand(m, []) is True  # вейтлист є, відгуків нема


def test_validate_demand_signal_from_feedback_only():
    m = Metrics(date="2026-07-31", waitlist_signups=0)
    assert validate_demand(m, [{"source": "X", "stars": 1, "date": "d",
                                "text": "t", "tags": ["low-res-export"]}]) is True


def test_validate_demand_no_signal_when_both_empty():
    m = Metrics(date="2026-07-31", waitlist_signups=0)
    assert validate_demand(m, []) is False


# --- граф: сигнал є -> делегує Product (acceptance 3a) ------------------------


def test_director_routes_to_product_and_aggregates():
    state = run_director(
        metrics_path=_METRICS, reviews_glob=_REVIEWS,
        enabled_workers=("product",), product_runner=_fake_product,
    )
    assert state["signal"] is True
    assert state["status"] == "ok"
    assert "product" in state["worker_results"]
    assert state["worker_results"]["product"]["status"] == "ok"


def test_director_needs_human_propagates_from_worker():
    def bad_product(**kwargs) -> dict:
        return {"status": "needs_human", "diff_path": None}

    state = run_director(
        metrics_path=_METRICS, reviews_glob=_REVIEWS,
        enabled_workers=("product",), product_runner=bad_product,
    )
    assert state["status"] == "needs_human"


# --- граф: сигналу нема -> no_signal (acceptance §4) --------------------------


def test_director_no_signal_stops_without_delegating(tmp_path):
    # порожні метрики (0 вейтлиста) + порожній reviews-glob -> гейт зупиняє
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({"date": "2026-07-31", "waitlist_signups": 0}),
                       encoding="utf-8")

    called = {"n": 0}

    def spy_product(**kwargs) -> dict:
        called["n"] += 1
        return {"status": "ok"}

    state = run_director(
        metrics_path="metrics.json", reviews_glob="none/*.md", root=tmp_path,
        enabled_workers=("product",), product_runner=spy_product,
    )
    assert state["status"] == "no_signal"
    assert called["n"] == 0  # воркер НЕ викликався
    assert state["worker_results"] == {}


# --- product-воркер справді делегує граф 2b (не копія) -----------------------


def test_product_worker_delegates_real_product_graph(tmp_path, monkeypatch):
    # дефолтний product_runner = run_product_agent (граф 2b). Мокаємо LLM-вузол
    # генерації у product_graph і preflight -> якщо їх викликано, граф 2b реально
    # прогнався всередині воркера.
    import server.product_graph as pgm
    from server.product_graph import Preset

    gen_calls = {"n": 0}

    def fake_generate(plan, feedback) -> Preset:
        gen_calls["n"] += 1
        return Preset(
            name="p", segment="s", style="minimal", palette=["#111111", "#EEEEEE"],
            layout_hint="centered", sample_title="T", sample_name="N", rationale="r",
        )

    def fake_pdf(_spec) -> bytes:
        return b"%PDF-1.4\n%%EOF\n"

    # підмінюємо дефолтний генератор і рендер у самому run_product_agent
    orig = pgm.run_product_agent

    def patched_run(**kwargs):
        kwargs.setdefault("generate", fake_generate)
        kwargs.setdefault("renderer", fake_pdf)
        kwargs.setdefault("out_dir", tmp_path / "presets")
        return orig(**kwargs)

    monkeypatch.setattr(pgm, "run_product_agent", patched_run)
    # director імпортував run_product_agent за посиланням -> патчимо і там
    import server.director_graph as dgm
    monkeypatch.setattr(dgm, "run_product_agent", patched_run)

    state = run_director(metrics_path=_METRICS, reviews_glob=_REVIEWS,
                         enabled_workers=("product",))
    assert state["status"] == "ok"
    assert gen_calls["n"] > 0  # LLM-вузол графа 2b справді виконався


# ============================================================================
# Milestone 3b: Marketing + Sales/Support воркери, паралелізм (§2.1)
# ============================================================================


def _fake_marketing(feedback, metrics) -> dict:
    return {"status": "ok", "draft": "Друкарський вектор без мила — CMYK/300 DPI."}


def _fake_sales_support(feedback) -> dict:
    return {"status": "ok", "answer": "Текст у кривих, CMYK через Ghostscript.",
            "question": "як гарантуєте якість?"}


def test_director_aggregates_all_three_workers():
    state = run_director(
        metrics_path=_METRICS, reviews_glob=_REVIEWS,
        product_runner=_fake_product,
        marketing_runner=_fake_marketing,
        sales_support_runner=_fake_sales_support,
    )
    assert state["status"] == "ok"
    assert set(state["worker_results"]) == {"product", "marketing", "sales_support"}
    assert state["worker_results"]["marketing"]["draft"]
    assert state["worker_results"]["sales_support"]["answer"]


def test_workers_run_in_parallel_not_sequentially():
    # §2.1: три воркери зі штучною затримкою -> сумарний час < суми часів,
    # що доводить fan-out (паралельно), а не послідовний виклик.
    import time

    delay = 0.3

    def slow(**_kwargs) -> dict:
        time.sleep(delay)
        return {"status": "ok"}

    def slow_mk(feedback, metrics) -> dict:
        time.sleep(delay)
        return {"status": "ok"}

    def slow_ss(feedback) -> dict:
        time.sleep(delay)
        return {"status": "ok"}

    t0 = time.perf_counter()
    state = run_director(
        metrics_path=_METRICS, reviews_glob=_REVIEWS,
        product_runner=slow, marketing_runner=slow_mk, sales_support_runner=slow_ss,
    )
    elapsed = time.perf_counter() - t0
    assert state["status"] == "ok"
    assert len(state["worker_results"]) == 3
    # 3 воркери × 0.3s: послідовно було б ~0.9s. Паралельно — біля 0.3s.
    assert elapsed < 3 * delay * 0.75, f"схоже на послідовний виклик: {elapsed:.2f}s"


def test_sales_support_reuses_phase1_retrieval(monkeypatch):
    # Sales/Support дефолт переюзає ask() з Ф1 support-бота, не новий retrieval.
    import server.director_graph as dgm

    calls = {"n": 0}

    def fake_ask(question, **kwargs) -> str:
        calls["n"] += 1
        return f"[доко-відповідь на: {question[:20]}...]"

    monkeypatch.setattr(dgm, "ask", fake_ask)
    state = run_director(
        metrics_path=_METRICS, reviews_glob=_REVIEWS,
        enabled_workers=("sales_support",),
    )
    assert state["status"] == "ok"
    assert calls["n"] == 1  # ask() Ф1 справді викликано
    assert "доко-відповідь" in state["worker_results"]["sales_support"]["answer"]
