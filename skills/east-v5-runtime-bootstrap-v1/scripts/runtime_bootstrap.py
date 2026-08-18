#!/usr/bin/env python3
"""Executable EAS-70 task bootstrap; prints redacted preflight evidence only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.runtime.bootstrap import RuntimeBootstrap, RuntimeBootstrapError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preflight"])
    parser.add_argument("--envelope-file", required=True)
    arguments = parser.parse_args()
    try:
        envelope = json.loads(Path(arguments.envelope_file).read_text(encoding="utf-8"))
        result = RuntimeBootstrap(ROOT, envelope).preflight().redacted()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeBootstrapError) as exc:
        # Stable error code only: neither checkout nor runtime-root path is emitted.
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
