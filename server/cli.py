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

    # Підхопити .env (PRINT_ICC_PROFILE тощо) для обох команд.
    from dotenv import load_dotenv

    load_dotenv()

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
