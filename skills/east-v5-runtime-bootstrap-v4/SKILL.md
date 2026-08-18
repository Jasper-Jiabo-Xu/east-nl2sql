---
name: east-v5-runtime-bootstrap-v4
description: Execute the EAST V5 v4 fail-closed controller for 010, 110, and 120, including a real no-business launcher-control preflight that creates and recovers one 110 control task.
---

# EAST V5 Runtime Bootstrap V4

Use the bundled controller from the Skill root. It is the only execution path.

```text
python3 scripts/controller.py claim-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py business-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py launcher-control-preflight --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root>
python3 scripts/controller.py orphan-audit --runtime-root <managed-root> --recovery-of <recovery-of.json>
python3 scripts/controller.py run-task --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root> --task-id <task-uuid> --runtime-id <runtime-uuid>
```

`launcher-control-preflight` calls the same outbox/create/observe function as `run-task`, but accepts only `launcher_control_envelope/v1`. It creates or recovers exactly one 110 control task and never registers an artifact, emits a business receipt, or launches 120. `run-task` stages output until a launcher outbox reaches `task_id`; only then commits a consumable receipt.
