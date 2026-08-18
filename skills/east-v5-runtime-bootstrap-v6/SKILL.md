---
name: east-v5-runtime-bootstrap-v6
description: Execute the EAST V5 v6 instruction-bound controller for 010, 110, and 120: accepted preflight must be followed by same-task committed run-task.
---

# EAST V5 Runtime Bootstrap V6

Use the bundled controller from the Skill root. It is the only execution path.

```text
python3 scripts/controller.py claim-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py business-preflight --envelope-file <envelope> --claim-file <claim>
python3 scripts/controller.py launcher-control-preflight --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root>
python3 scripts/controller.py orphan-audit --runtime-root <managed-root> --recovery-of <recovery-of.json>
python3 scripts/controller.py run-task --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root> --task-id <task-uuid> --runtime-id <runtime-uuid>
```

`launcher-control-preflight` calls the same outbox/create/observe function as `run-task`, but accepts only `launcher_control_envelope/v1`. It creates its `--description-file` descriptor privately under the current controller cwd (0600), never uses `--allow-external-file`, and never registers an artifact, emits a business receipt, or launches 120.

For `task_input_envelope/v6`, an accepted preflight is only a gate. The claimed 010, 110, or 120 task must call `run-task` in that same task. 010 and 110 may finish only after `stage=committed`, receipt, and a unique next-task UUID; 120 requires a committed terminal receipt. `instructions_sha256` must equal the manifest hash for the claimed target; route intent, comments, and manual control-plane writes never satisfy this contract.
