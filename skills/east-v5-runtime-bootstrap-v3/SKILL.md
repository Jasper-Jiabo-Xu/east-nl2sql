---
name: east-v5-runtime-bootstrap-v3
description: Execute the recoverable, fail-closed EAST V5 v3 launcher controller for 010, 110, and 120, including no-write launcher-control preflight and orphan audit.
---

# EAST V5 Runtime Bootstrap V3

Use the bundled controller from the Skill root. It is the only execution path.

```text
python3 scripts/controller.py claim-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py business-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py launcher-control-preflight --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root>
python3 scripts/controller.py orphan-audit --runtime-root <managed-root> --recovery-of <recovery-of.json>
python3 scripts/controller.py run-task --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root> --task-id <task-uuid> --runtime-id <runtime-uuid>
```

`business-preflight`, `launcher-control-preflight`, and `orphan-audit` are read-only with respect to business artifacts. `run-task` stages output until a launcher outbox reaches `task_id`; only then commits a consumable receipt.
