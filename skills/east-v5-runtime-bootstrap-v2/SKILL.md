---
name: east-v5-runtime-bootstrap-v2
description: Execute the self-contained, fail-closed EAST V5 runtime controller for 010, 110, and 120. Use it for v2 claim checks, no-write business preflight, and controller-owned artifact/receipt/task handoff; never substitute a checkout, network fetch, local project directory, or prompt for the bundled entrypoint.
---

# EAST V5 Runtime Bootstrap V2

Use the bundled controller from the Skill root. It is the only execution path.

```text
python3 scripts/controller.py claim-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py business-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py run-task --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root> --task-id <task-uuid> --runtime-id <runtime-uuid>
```

`business-preflight` is read-only. `run-task` verifies the immutable manifest, exact candidate head, workspace Skill ID, target/provider, and root binding before it creates any local artifact. It alone may register, read back, receipt, and launch the next task. A nonzero result is fail-closed.
