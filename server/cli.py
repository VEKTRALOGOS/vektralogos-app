"""CLI Фази 0.

  python -m server.cli render examples/hello.json -o print.pdf
  python -m server.cli prompt "візитка для кав'ярні" -o card.pdf
"""

from __future__ import annotations

import argparse
import sys

from .preflight import preflight
from .render import render
from .schema import CanvasJSON


def _print_report(report) -> None:
    mark = "✅ OK" if report.ok else "❌ НЕ ГОТОВО"
    print(f"Preflight: {mark} ({len(report.issues)} зауважень)")
    for i in report.issues:
        icon = {"error": "❌", "warn": "⚠️ ", "info": "ℹ️ "}[i.level]
        print(f"  {icon} [{i.code}] {i.message}")


def _cmd_render(args: argparse.Namespace) -> int:
    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = CanvasJSON.model_validate_json(fh.read())
    pdf = render(spec)
    with open(args.output, "wb") as fh:
        fh.write(pdf)
    print(f"OK: {args.spec} -> {args.output} ({len(pdf)} байт)")
    _print_report(preflight(spec, pdf))
    return 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = CanvasJSON.model_validate_json(fh.read())
    pdf = render(spec) if not args.no_render else None
    report = preflight(spec, pdf)
    _print_report(report)
    return 0 if report.ok else 1


def _cmd_preflight_fix(args: argparse.Namespace) -> int:
    from .preflight_agent import preflight_agent

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = CanvasJSON.model_validate_json(fh.read())
    result = preflight_agent(spec)
    print(f"Агент: status={result.status}, ітерацій={result.iterations}")
    _print_report(result.report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(result.spec.model_dump_json(indent=2))
        print(f"Виправлений Canvas JSON -> {args.output}")
    return 0 if result.status == "ok" else 1


def _cmd_ask(args: argparse.Namespace) -> int:
    from .support_bot import ask

    print(ask(args.question, k=args.k))
    return 0


def _cmd_product_run(args: argparse.Namespace) -> int:
    from .product_graph import run_product_agent

    state = run_product_agent(out_dir=args.output)
    print(f"Product-агент: status={state['status']}")
    if state.get("plan"):
        print(f"План: {state['plan']}")
    if state["status"] == "ok":
        print(f"Пресет + опис -> {state['diff_path']} (та .md поруч)")
    elif state["status"] == "no_feedback":
        print("Відгуків не знайдено — пресет не генерувався (чесна зупинка).")
    else:
        pr = state.get("preflight_result")
        print(f"Потрібна людина: preflight={pr.status if pr else '—'}")
    return 0 if state["status"] in ("ok", "no_feedback") else 1


def _cmd_propose_and_open_pr(args: argparse.Namespace) -> int:
    from .product_graph import run_product_with_approval

    def decide(diff_text: str, desc_text: str) -> str:
        print("\n=== ПРЕСЕТ (diff) ===\n" + diff_text)
        print("\n=== ОПИС PR ===\n" + desc_text)
        print("\n⚠️  Це створить РЕАЛЬНИЙ PR (gh pr create).")
        ans = input("Введіть слово 'approve' для підтвердження (будь-що інше — reject): ")
        return "approve" if ans.strip() == "approve" else "reject"

    state = run_product_with_approval(out_dir=args.output, decide=decide)
    print(f"\nСтатус: {state['status']}")
    if state["status"] == "applied":
        print(f"PR створено: {state.get('pr_url')}")
    elif state["status"] == "rejected":
        print(f"Відхилено. diff лишився на диску: {state.get('diff_path')}")
    return 0 if state["status"] in ("applied", "rejected", "no_feedback") else 1


def _cmd_evals(args: argparse.Namespace) -> int:
    from .evals import run_evals

    results = run_evals()
    for r in results:
        icon = "✅" if r.passed else "❌"
        print(f"  {icon} {r.name}: {r.detail}")
    failed = [r for r in results if not r.passed]
    print(f"Evals: {len(results) - len(failed)}/{len(results)} пройдено")
    return 0 if not failed else 1


def _cmd_director_run(args: argparse.Namespace) -> int:
    from .director_graph import run_director

    state = run_director()
    print(f"Director: status={state['status']}, signal={state['signal']}")
    for name, res in (state.get("worker_results") or {}).items():
        print(f"  воркер {name}: {res}")
    if state["status"] == "no_signal":
        print("Гейт validate_demand: сигналу нема (нема ні вейтлиста, ні відгуків).")
    return 0 if state["status"] in ("ok", "no_signal") else 1


def _cmd_prompt(args: argparse.Namespace) -> int:
    # Ліниво: тягне anthropic лише для цього шляху.
    from .brief import prompt_to_brief
    from .prompt_to_canvas import brief_from_prompt_to_canvas

    brief = prompt_to_brief(args.prompt)
    if args.brief_out:
        with open(args.brief_out, "w", encoding="utf-8") as fh:
            fh.write(brief.model_dump_json(indent=2))
    spec = brief_from_prompt_to_canvas(brief, size=args.size)
    if args.spec_out:
        with open(args.spec_out, "w", encoding="utf-8") as fh:
            fh.write(spec.model_dump_json(indent=2))
    pdf = render(spec)
    with open(args.output, "wb") as fh:
        fh.write(pdf)
    print(f"OK: prompt -> {args.output} ({len(pdf)} байт, розмір {args.size})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="server.cli", description="Vektralogos Фаза 0")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="Canvas JSON -> print.pdf")
    p_render.add_argument("spec", help="Шлях до Canvas JSON")
    p_render.add_argument("-o", "--output", default="print.pdf")
    p_render.set_defaults(func=_cmd_render)

    p_prompt = sub.add_parser("prompt", help="prompt -> DesignBrief -> Canvas JSON -> print.pdf")
    p_prompt.add_argument("prompt", help="Текстовий запит клієнта")
    p_prompt.add_argument("-o", "--output", default="print.pdf")
    p_prompt.add_argument("--size", default="a6", help="Розмір полотна: a4/a5/a6/card")
    p_prompt.add_argument("--brief-out", help="Куди зберегти DesignBrief від LLM")
    p_prompt.add_argument("--spec-out", help="Куди зберегти згенерований Canvas JSON")
    p_prompt.set_defaults(func=_cmd_prompt)

    p_pre = sub.add_parser("preflight", help="Перевірити Canvas JSON (+ рендер) на придатність до друку")
    p_pre.add_argument("spec", help="Шлях до Canvas JSON")
    p_pre.add_argument("--no-render", action="store_true", help="Не рендерити PDF — перевіряти лише спеку")
    p_pre.set_defaults(func=_cmd_preflight)

    p_fix = sub.add_parser(
        "preflight-fix",
        help="Прогнати preflight-агента (issue -> fix -> re-check) і зберегти виправлений spec",
    )
    p_fix.add_argument("spec", help="Шлях до Canvas JSON")
    p_fix.add_argument("-o", "--output", help="Куди зберегти виправлений Canvas JSON")
    p_fix.set_defaults(func=_cmd_preflight_fix)

    p_ask = sub.add_parser("ask", help="Support-бот: питання про проєкт (RAG по доці репо)")
    p_ask.add_argument("question", help="Питання українською/іншою мовою")
    p_ask.add_argument("-k", type=int, default=5, help="Скільки чанків у контекст (дефолт 5)")
    p_ask.set_defaults(func=_cmd_ask)

    p_prod = sub.add_parser(
        "product-run",
        help="Product-агент (Ф2b): відгуки -> план -> пресет -> preflight -> diff (без PR)",
    )
    p_prod.add_argument("-o", "--output", default="presets", help="Куди писати пресет+опис")
    p_prod.set_defaults(func=_cmd_product_run)

    p_dir = sub.add_parser(
        "director-run",
        help="Director-агент (Ф3): гейт спроса -> делегує воркерам -> агрегує",
    )
    p_dir.set_defaults(func=_cmd_director_run)

    p_ev = sub.add_parser(
        "evals",
        help="Детерміновані evals якості тракту на фікстурах (Ф4b), без мережі/gs",
    )
    p_ev.set_defaults(func=_cmd_evals)

    p_pr = sub.add_parser(
        "propose-and-open-pr",
        help="Product-агент + approval-гейт (Ф4a): пресет -> diff -> approve -> gh pr create",
        description=("СИНХРОННА модель (спека §2.2): команда генерує пресет, показує "
                     "diff і опис, і НЕ повертає керування, поки ви не введете у ЦЬОМУ "
                     "Ж терміналі слово 'approve' (реальний gh pr create) або будь-що "
                     "інше (reject; diff лишається на диску). Один процес, підтвердження "
                     "одразу — без асинхронного очікування."),
    )
    p_pr.add_argument("-o", "--output", default="presets", help="Куди писати пресет+опис")
    p_pr.set_defaults(func=_cmd_propose_and_open_pr)

    # Підхопити .env (PRINT_ICC_PROFILE тощо) для обох команд.
    from dotenv import load_dotenv

    load_dotenv()

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
