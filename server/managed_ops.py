"""Ops-агент на Claude Managed Agents: host-side custom tools + розклад (Ф5b).

Друкарський тракт (preflight/render) потребує НАШОГО коду і Ghostscript, яких
немає в cloud-пісочниці CMA. Тому віддаємо їх агенту як **host-side custom
tools** (CMA Pattern 9): агент емітить `agent.custom_tool_use`, НАШ оркестратор
виконує `preflight_agent_graph`/`render` у нас (де є gs і код) і повертає
`user.custom_tool_result`. gs і код лишаються host-side; переюз готових Ф2/Ф0
функцій без переписування під пісочницю.

`deployments.create` (cron) дає нативний автономний ops-каданс: кожне
спрацювання створює сесію, що запускає перевірку.

⚠️ ВАРТІСТЬ (спека §2): scheduled deployment тарифікується **безстроково**, доки
його не вимкнено — тому окремий «go» Антона з планом вимкнення (§2.1–2.2), і
`teardown_deployment` (pause+archive) обов'язковий (§2.3). Усе нижче — конфіги й
host-side dispatch, БЕЗ live-створення ресурсів; live-шлях за гейтом `allow_live`.
"""

from __future__ import annotations

from typing import Callable

from .preflight_graph import preflight_agent_graph
from .render import render
from .schema import CanvasJSON

MODEL = "claude-opus-5"
OPS_AGENT_NAME = "Vektralogos Print-Ops"

OPS_SYSTEM = (
    "Ти — ops-агент Vektralogos. Твоє завдання — перевіряти придатність дизайну "
    "до друку. Використовуй інструмент run_preflight (він проганяє друкарський "
    "preflight-агент на нашому боці), за потреби render_pdf. Спирайся лише на "
    "результати інструментів; поверни короткий звіт зі статусом (ok/needs_human) "
    "і переліком issue-кодів."
)

# Схема host-side custom tools. Виконання — у нас (dispatch_custom_tool), не в
# пісочниці; агент лише викликає за іменем зі spec-об'єктом (CanvasJSON).
_SPEC_SCHEMA = {
    "type": "object",
    "properties": {"spec": {"type": "object", "description": "Canvas JSON дизайну"}},
    "required": ["spec"],
}
CUSTOM_TOOLS = [
    {"type": "custom", "name": "run_preflight",
     "description": "Прогнати друкарський preflight-агент (issue->fix->re-check) на нашому боці. Вхід: {spec: CanvasJSON}.",
     "input_schema": _SPEC_SCHEMA},
    {"type": "custom", "name": "render_pdf",
     "description": "Зібрати друкарський PDF (vector, CMYK/ICC) на нашому боці. Вхід: {spec: CanvasJSON}.",
     "input_schema": _SPEC_SCHEMA},
]


def build_ops_agent_config(*, model: str = MODEL, name: str = OPS_AGENT_NAME) -> dict:
    """Параметри `client.beta.agents.create(**config)` для ops-агента з
    host-side custom tools (створюється один раз, версіюється)."""
    return {"name": name, "model": model, "system": OPS_SYSTEM, "tools": CUSTOM_TOOLS}


# --- host-side dispatch: НАШ оркестратор виконує інструмент (Pattern 9) --------


def _text_result(text: str, *, is_error: bool = False) -> dict:
    """user.custom_tool_result content (текстовий блок)."""
    return {"content": [{"type": "text", "text": text}], "is_error": is_error}


def dispatch_custom_tool(
    name: str,
    tool_input: dict,
    *,
    renderer: Callable[[CanvasJSON], bytes] = render,
    preflight_runner: Callable[..., object] = preflight_agent_graph,
) -> dict:
    """Виконує host-side custom tool за іменем; повертає content+is_error для
    `user.custom_tool_result`. `renderer` інжектується (у тестах — фейк без gs).
    """
    try:
        spec = CanvasJSON.model_validate(tool_input["spec"])
    except Exception as e:  # noqa: BLE001 — невалідний spec = помилка інструмента
        return _text_result(f"Невалідний spec: {e}", is_error=True)

    if name == "run_preflight":
        result = preflight_runner(spec, renderer=renderer)
        codes = [i.code for i in result.report.issues]
        return _text_result(
            f"preflight: status={result.status}, iterations={result.iterations}, "
            f"issues={codes}"
        )
    if name == "render_pdf":
        pdf = renderer(spec)
        return _text_result(f"render: PDF зібрано, {len(pdf)} байт")
    return _text_result(f"Невідомий інструмент: {name}", is_error=True)


# --- scheduled deployment (cron) ----------------------------------------------

_OPS_KICKOFF = ("Перевір придатність до друку sample-дизайну: виклич run_preflight "
                "і поверни короткий звіт зі статусом та issue-кодами.")


def build_deployment_config(
    *, agent_id: str, environment_id: str,
    cron: str = "0 6 * * 1", timezone: str = "Europe/Kyiv",
    name: str = "Vektralogos weekly print-ops",
) -> dict:
    """Параметри `client.beta.deployments.create(**config)` — щотижневий ops-каданс
    (дефолт: понеділок 06:00 Europe/Kyiv). Кожне спрацювання створює сесію."""
    return {
        "name": name,
        "agent": agent_id,
        "environment_id": environment_id,
        "initial_events": [
            {"type": "user.message", "content": [{"type": "text", "text": _OPS_KICKOFF}]}
        ],
        "schedule": {"type": "cron", "expression": cron, "timezone": timezone},
    }


def teardown_deployment(client, deployment_id: str) -> None:
    """Вимикає розклад: pause (зупинити тригери) -> archive (термінально). §2.3 —
    без цього кроку deployment тарифікується безстроково."""
    try:
        client.beta.deployments.pause(deployment_id)
    except Exception:  # noqa: BLE001
        pass
    client.beta.deployments.archive(deployment_id)


# --- live-шлях (ГЕЙТ: окремий «go» для 5b, §2.1) ------------------------------


def create_scheduled_ops(*, allow_live: bool = False, **_kwargs):
    """Live-створення cron-deployment. НЕ викликати без окремого «go» Антона:
    cron тарифікується безстроково, потрібен план вимкнення (спека §2.1–2.2)."""
    raise RuntimeError(
        "Live scheduled deployment 5b заблоковано. Cron тарифікується "
        "БЕЗСТРОКОВО (спека §2.1) — потрібен ОКРЕМИЙ «go» Антона з планом "
        "вимкнення (teardown_deployment) і оновлений anthropic SDK. "
        "allow_live=True ставити лише після цього."
    ) if not allow_live else _create_scheduled_ops_live(**_kwargs)


def _create_scheduled_ops_live(*, github_token: str, cron: str = "0 6 * * 1",
                               timezone: str = "Europe/Kyiv"):  # pragma: no cover
    """Реальне створення (агент + env + deployment). Виконується лише під
    allow_live=True після «go». Не покрито тестами (мережа/платно)."""
    import anthropic

    from .managed_support import session_resources

    client = anthropic.Anthropic()
    agent = client.beta.agents.create(**build_ops_agent_config())
    environment = client.beta.environments.create(
        name="vektralogos-ops",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    deployment = client.beta.deployments.create(
        **build_deployment_config(agent_id=agent.id, environment_id=environment.id,
                                  cron=cron, timezone=timezone)
    )
    return {"agent_id": agent.id, "environment_id": environment.id,
            "deployment_id": deployment.id,
            "note": "cron активний і ТАРИФІКУЄТЬСЯ — виклич teardown_deployment після ADR"}
