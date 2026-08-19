---
name: east-v5-runtime-bootstrap-v11
description: Execute the EAST V5 v11 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight.
---

# EAST V5 Runtime Bootstrap V11

Use the bundled controller from the Skill root. It is the only execution path.

```text
python3 scripts/controller.py full-preflight --claims-file <17-claims.json> --component-receipt-file <000-receipt.json> --runtime-root <managed-root>
python3 scripts/controller.py run-task --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root> --task-id <task-uuid>
python3 scripts/controller.py inspect-run --runtime-root <managed-root> --run-id <run-id>
```

The graph is defined only by `config/full-runtime-graph.json`: 17 real Agents (`010,110,120,130,140,150,160,170,180,210,220,230,241,242,251,252,260`) and fixed component `000`. `000` supplies a signed component receipt; it is never a task target. The controller accepts only `runtime_graph_envelope/v11`, validates catalog edges and barriers before persisting a receipt, and derives every successor from the graph. It never accepts a caller-proposed next task.

Before any business task, `full-preflight` must verify the 17 exact real Agent UUID/runtime bindings, 17 instruction hashes, 17 enabled v11 Skill bindings, config hash, manifest hash, shared 0700 daemon root and the fixed 000 receipt. A failed or drifting preflight persists no run and returns zero tasks. The preflight token is bound to the root, manifest and all claims; each envelope must carry it.

Question-SQL requires both 170 and 180 receipts before 110 may enter the data stage. Event data and ORM lanes are independent after 230 but both are required at 260. Foundation uses its separate 210→220→241→242→260→210→010 chain and forbids 230/251/252. Every failure is mapped to its affected upstream restart node; attempts are limited to three, duplicate key replays add no tasks, unknown/duplicate/drifting receipts fail closed, and terminal completion has no successors.
