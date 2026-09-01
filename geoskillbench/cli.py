from __future__ import annotations

import argparse
import json
from pathlib import Path

from geoskillbench.runner import TestRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geoskillbench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("path")
    run_parser.add_argument("--output", default="reports")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("path")

    list_tools_parser = subparsers.add_parser("list-tools")
    list_tools_parser.add_argument("path")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    runner = TestRunner()
    if args.command == "validate":
        print(json.dumps(runner.validate(args.path), ensure_ascii=False, indent=2))
        return
    if args.command == "list-tools":
        print(json.dumps(runner.list_tools(args.path), ensure_ascii=False, indent=2))
        return
    if args.command == "run":
        path = Path(args.path)
        if path.is_dir():
            results = []
            for file_path in sorted(path.rglob("*.yml")) + sorted(path.rglob("*.yaml")):
                results.append(TestRunner().run(str(file_path), args.output).model_dump())
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return
        result = runner.run(args.path, args.output)
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
