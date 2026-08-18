#!/usr/bin/env python3
"""The only Skill-bundle runner: claim preflight, never business execution."""
from __future__ import annotations

import sys

from claim_preflight import main as claim_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments.pop(0) != "claim-preflight":
        print("RUNTIME_SKILL_RUNNER_COMMAND_INVALID", file=sys.stderr)
        return 2
    return claim_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
