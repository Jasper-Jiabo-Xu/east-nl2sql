#!/usr/bin/env python3
"""Single executable entrypoint for the immutable v11 full-graph Skill."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("east_v5/governance.py", "east_v5/runtime/graph_controller.py", "config/full-runtime-graph.json", "manifest.json")


def _load(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RUNTIME_TASK_INPUT_INVALID")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("full-preflight", "run-task", "inspect-run"))
    parser.add_argument("--envelope-file")
    parser.add_argument("--claim-file")
    parser.add_argument("--runtime-root")
    parser.add_argument("--task-id")
    parser.add_argument("--runtime-id")
    parser.add_argument("--launcher-record")
    parser.add_argument("--launcher-fail", action="store_true")
    parser.add_argument("--root-binding-id")
    parser.add_argument("--issue-id")
    parser.add_argument("--run-id")
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--reference-file")
    parser.add_argument("--receipt-task-id")
    parser.add_argument("--claims-file")
    parser.add_argument("--component-receipt-file")
    args = parser.parse_args(argv)
    if not all((ROOT / relative).is_file() for relative in REQUIRED):
        print("RUNTIME_SKILL_DEPENDENCY_MISSING", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    from east_v5.runtime.graph_controller import GraphController, GraphError
    try:
        if not args.runtime_root:
            raise GraphError("RUNTIME_RUN_ARGUMENT_MISSING")
        controller = GraphController(ROOT, Path(args.runtime_root))
        if args.command == "full-preflight":
            if not args.claims_file or not args.component_receipt_file:
                raise GraphError("RUNTIME_RUN_ARGUMENT_MISSING")
            print(json.dumps(controller.full_preflight(_load(args.claims_file), _load(args.component_receipt_file)), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "inspect-run":
            if not args.run_id:
                raise GraphError("RUNTIME_RUN_ARGUMENT_MISSING")
            print(json.dumps(controller.inspect(args.run_id), ensure_ascii=False, sort_keys=True))
            return 0
        if not args.envelope_file or not args.claim_file or not args.task_id:
            raise GraphError("RUNTIME_RUN_ARGUMENT_MISSING")
        print(json.dumps(controller.run_task(_load(args.envelope_file), _load(args.claim_file), args.task_id), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, GraphError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
