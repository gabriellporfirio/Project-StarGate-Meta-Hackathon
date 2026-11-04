"""Command line for the stargate package.

Commands: doctor, scan, research, plan, build, install-plan.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .build.gate import run_build
from .doctor import run_doctor
from .install import run_install_plan
from .plan import run_plan
from .research import run_research
from .scan import run_scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stargate", description="StarGate platform tools"
    )
    parser.add_argument(
        "--version", action="version", version="stargate " + __version__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "doctor",
        help="check interpreter, plan pin, Meta credential and Meta inference",
    )
    scan = sub.add_parser(
        "scan", help="describe a simulated phone (M1: --sim only)"
    )
    scan.add_argument(
        "--sim",
        default=None,
        help="simulated device name (budget, flagship, old32, locked)",
    )
    scan.add_argument(
        "--json",
        action="store_true",
        help="print the complete validated profile as JSON",
    )
    research = sub.add_parser(
        "research", help="research a device with Meta (M2: agent mode)"
    )
    research.add_argument("--codename", default=None)
    research.add_argument("--device", default=None)
    research.add_argument("--sim", default=None)
    research.add_argument("--json", action="store_true")
    research.add_argument("--json-out", default=None)
    research.add_argument("--record", default=None)
    research.add_argument("--replay", default=None)
    research.add_argument("--no-cache", action="store_true")
    plan = sub.add_parser(
        "plan", help="strategy and fit plan for a phone (M3)"
    )
    plan.add_argument("--sim", default=None)
    plan.add_argument("--profile", default=None)
    plan.add_argument("--services", default=None)
    plan.add_argument("--research", default=None)
    plan.add_argument("--explain", action="store_true")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--json-out", default=None)
    build = sub.add_parser(
        "build", help="build a StarGate image from an accepted plan (M4)"
    )
    build.add_argument("--plan", default=None)
    build.add_argument("--out", default=None)
    build.add_argument("--assistant-manifest", default=None)
    install_plan = sub.add_parser(
        "install-plan", help="draft an install plan with Meta (M5)"
    )
    install_plan.add_argument("--sim", default=None)
    install_plan.add_argument("--profile", default=None)
    install_plan.add_argument("--plan", default=None)
    install_plan.add_argument("--record", default=None)
    install_plan.add_argument("--replay", default=None)
    install_plan.add_argument("--json-out", default=None)
    install_plan.add_argument("--diagnose", action="store_true")
    install_plan.add_argument("--diag-out", default=None)
    app = sub.add_parser(
        "app", help="guided app on 127.0.0.1 (M6 platform)"
    )
    app.add_argument("--port", type=int, default=8770)
    app.add_argument("--forwarded-host", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        exit_code, report = run_doctor()
        print(report, end="")
        return exit_code
    if args.command == "scan":
        exit_code, output = run_scan(args.sim, bool(args.json))
        print(output, end="")
        return exit_code
    if args.command == "research":
        exit_code, stdout_text, stderr_text = run_research(
            codename=args.codename,
            device=args.device,
            sim=args.sim,
            json_only=bool(args.json),
            json_out=args.json_out,
            record_path=args.record,
            replay_path=args.replay,
            no_cache=bool(args.no_cache),
        )
        if stdout_text:
            print(stdout_text, end="")
        if stderr_text:
            print(stderr_text, end="", file=sys.stderr)
        return exit_code
    if args.command == "plan":
        exit_code, stdout_text, stderr_text = run_plan(
            sim=args.sim,
            profile_path=args.profile,
            services_text=args.services,
            research_path=args.research,
            explain=bool(args.explain),
            json_only=bool(args.json),
            json_out=args.json_out,
        )
        if stdout_text:
            print(stdout_text, end="")
        if stderr_text:
            print(stderr_text, end="", file=sys.stderr)
        return exit_code
    if args.command == "app":
        from .appserver import run_app
        return run_app(["--port", str(args.port)] +
                       (["--forwarded-host", args.forwarded_host]
                        if args.forwarded_host else []))
    if args.command == "build":
        if args.plan is None or args.out is None:
            print("error: stargate build needs --plan and --out",
                  file=sys.stderr)
            return 6
        exit_code, stdout_text, stderr_text = run_build(
            plan_path=args.plan,
            out_dir=args.out,
            assistant_manifest_path=args.assistant_manifest,
        )
        if stdout_text:
            print(stdout_text, end="")
        if stderr_text:
            print(stderr_text, end="", file=sys.stderr)
        return exit_code
    if args.command == "install-plan":
        exit_code, stdout_text, stderr_text = run_install_plan(
            sim=args.sim,
            profile_path=args.profile,
            plan_path=args.plan,
            json_out=args.json_out,
            record_path=args.record,
            replay_path=args.replay,
            diagnose=bool(args.diagnose),
            diag_out=args.diag_out,
        )
        if stdout_text:
            print(stdout_text, end="")
        if stderr_text:
            print(stderr_text, end="", file=sys.stderr)
        return exit_code
    raise AssertionError("unknown command: %r" % (args.command,))
