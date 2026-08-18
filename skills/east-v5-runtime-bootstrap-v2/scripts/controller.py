#!/usr/bin/env python3
"""Single executable entrypoint for the immutable v2 workspace Skill."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("east_v5/governance.py", "east_v5/artifacts/registry.py", "east_v5/runtime/controller_core.py", "manifest.json")


def _load(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RUNTIME_TASK_INPUT_INVALID")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("claim-preflight", "business-preflight", "run-task"))
    parser.add_argument("--envelope-file", required=True)
    parser.add_argument("--claim-file", required=True)
    parser.add_argument("--runtime-root")
    parser.add_argument("--task-id")
    parser.add_argument("--runtime-id")
    parser.add_argument("--launcher-record")
    parser.add_argument("--launcher-fail", action="store_true")
    args = parser.parse_args(argv)
    if not all((ROOT / relative).is_file() for relative in REQUIRED):
        print("RUNTIME_SKILL_DEPENDENCY_MISSING", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    from east_v5.runtime import ControllerError, RuntimeController
    try:
        controller = RuntimeController(ROOT, _load(args.envelope_file), _load(args.claim_file))
        if args.command != "run-task":
            print(json.dumps(controller.preflight(business=args.command == "business-preflight"), ensure_ascii=False, sort_keys=True))
            return 0
        if not args.runtime_root or not args.task_id or not args.runtime_id:
            raise ControllerError("RUNTIME_RUN_ARGUMENT_MISSING")
        record = Path(args.launcher_record).resolve() if args.launcher_record else None
        print(json.dumps(controller.run_task(Path(args.runtime_root).resolve(), task_id=args.task_id, runtime_id=args.runtime_id, launcher_record=record, launcher_fail=args.launcher_fail), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, ControllerError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
