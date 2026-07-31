"""Тести Ops-агента на CMA (Фаза 5b) — усе офлайн, БЕЗ live-створення ресурсів.

Host-side custom tools реально делегують наш Ф2 preflight-граф (з фейковим
renderer замість Ghostscript). Live scheduled deployment — за гейтом.
"""

from __future__ import annotations

import pytest

from server.managed_ops import (
    CUSTOM_TOOLS,
    OPS_AGENT_NAME,
    build_deployment_config,
    build_ops_agent_config,
    create_scheduled_ops,
    dispatch_custom_tool,
    teardown_deployment,
)
from server.schema import CanvasJSON


def _fake_pdf(_spec: CanvasJSON) -> bytes:
    return b"%PDF-1.4\n%%EOF\n"


def _good_spec() -> dict:
    return {
        "version": "1.0",
        "canvas": {"width_mm": 90, "height_mm": 50, "bleed_mm": 3},
        "fonts": [{"family": "Noto Sans", "file": "NotoSans-Regular.ttf"}],
        "elements": [{"type": "rect", "x_mm": 5, "y_mm": 5, "width_mm": 40,
                      "height_mm": 10, "fill": {"cmyk": [0, 0, 0, 1]}}],
    }


# --- конфіг агента -----------------------------------------------------------


def test_ops_agent_config_has_custom_tools():
    cfg = build_ops_agent_config()
    assert cfg["name"] == OPS_AGENT_NAME
    assert cfg["model"] == "claude-opus-5"
    names = {t["name"] for t in cfg["tools"]}
    assert names == {"run_preflight", "render_pdf"}
    assert all(t["type"] == "custom" for t in cfg["tools"])


# --- host-side dispatch: делегує наш Ф2 preflight-граф ------------------------


def test_dispatch_run_preflight_delegates_and_reports_ok():
    res = dispatch_custom_tool("run_preflight", {"spec": _good_spec()}, renderer=_fake_pdf)
    assert res["is_error"] is False
    text = res["content"][0]["text"]
    assert "status=ok" in text


def test_dispatch_run_preflight_reports_needs_human_on_bad_pdf():
    # renderer, що дає RGB-PDF -> rgb_in_print (error) -> needs_human
    from server.render import render_vector_pdf

    import os
    path = os.path.join(os.path.dirname(__file__), "..", "examples", "hello.json")
    spec = CanvasJSON.model_validate_json(open(path, encoding="utf-8").read())
    res = dispatch_custom_tool("run_preflight", {"spec": spec.model_dump()},
                               renderer=render_vector_pdf)
    assert "needs_human" in res["content"][0]["text"]
    assert "rgb_in_print" in res["content"][0]["text"]


def test_dispatch_render_pdf_reports_bytes():
    res = dispatch_custom_tool("render_pdf", {"spec": _good_spec()}, renderer=_fake_pdf)
    assert res["is_error"] is False
    assert "байт" in res["content"][0]["text"]


def test_dispatch_unknown_tool_is_error():
    res = dispatch_custom_tool("delete_everything", {"spec": _good_spec()}, renderer=_fake_pdf)
    assert res["is_error"] is True
    assert "Невідомий" in res["content"][0]["text"]


def test_dispatch_invalid_spec_is_error():
    res = dispatch_custom_tool("run_preflight", {"spec": {"bad": "shape"}}, renderer=_fake_pdf)
    assert res["is_error"] is True


# --- deployment config (cron) ------------------------------------------------


def test_deployment_config_has_cron_and_kickoff():
    dep = build_deployment_config(agent_id="agent_1", environment_id="env_1")
    assert dep["agent"] == "agent_1" and dep["environment_id"] == "env_1"
    assert dep["schedule"]["type"] == "cron"
    assert dep["schedule"]["expression"] == "0 6 * * 1"
    assert dep["initial_events"][0]["type"] == "user.message"


def test_deployment_config_custom_cron():
    dep = build_deployment_config(agent_id="a", environment_id="e",
                                  cron="30 5 * * *", timezone="UTC")
    assert dep["schedule"]["expression"] == "30 5 * * *"
    assert dep["schedule"]["timezone"] == "UTC"


# --- teardown (§2.3): pause -> archive ---------------------------------------


def test_teardown_deployment_pauses_then_archives():
    order = []

    class _Deployments:
        def pause(self, did):
            order.append(("pause", did))

        def archive(self, did):
            order.append(("archive", did))

    class _Client:
        class beta:  # noqa: N801
            deployments = _Deployments()

    teardown_deployment(_Client(), "depl_1")
    assert order == [("pause", "depl_1"), ("archive", "depl_1")]


# --- гейт: live заблоковано без окремого «go» --------------------------------


def test_live_scheduled_ops_blocked_without_allow_live():
    with pytest.raises(RuntimeError, match="БЕЗСТРОКОВО|заблоковано|окремий"):
        create_scheduled_ops(github_token="ghp_x")
