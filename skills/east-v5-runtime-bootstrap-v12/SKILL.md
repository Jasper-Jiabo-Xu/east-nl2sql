---
name: east-v5-runtime-bootstrap-v12
description: Execute the EAST V5 v12 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight.
---

# EAST V5 Runtime Bootstrap V12

Use the bundled controller from the Skill root. It is the only execution path.

```text
python3 scripts/controller.py full-preflight --claims-file <17-claims.json> --component-receipt-file <000-receipt.json> --runtime-root <managed-root>
python3 scripts/controller.py run-task --envelope-file <envelope> --claim-file <claim> --runtime-root <managed-root> --task-id <task-uuid>
python3 scripts/controller.py inspect-run --runtime-root <managed-root> --run-id <run-id>
```

The graph is defined only by `config/full-runtime-graph.json` and the approved authority baseline is `config/authority-matrix-v2.json`: 17 real Agents (`010,110,120,130,140,150,160,170,180,210,220,230,241,242,251,252,260`) and fixed component `000`. The matrix preserves the immutable v1 audit hash, records the sole approved 140 correction, and binds UUID/runtime/instruction hash/approved Skill provenance before a claim is accepted. `000` supplies a signed component receipt; it is never a task target. The controller accepts only `runtime_graph_envelope/v12`, validates catalog edges and barriers before persisting a receipt, and derives every successor from the graph. It never accepts a caller-proposed next task.

Skill approval has two frozen representations. `authority-matrix-v2.json` deliberately retains logical Skill names for audit provenance. `config/skill-identity-resolver-v1.json` is the separately versioned, manifest-hashed mapping from those approved names to workspace Skill UUIDs. The resolver is exact and fail-closed: claims carry only UUIDs, never logical names or caller-supplied mappings. A missing, drifted, malformed, unknown, duplicate, or unmapped resolver value rejects before any state is written.

Before any business task, `full-preflight` must verify the 17 exact real Agent UUID/runtime bindings, 17 instruction hashes, and each Agent's complete enabled-Skill inventory. The inventory is order-normalized and must equal the resolver-derived approved workspace Skill UUIDs plus exactly the newly installed v12 Skill UUID. `claims.skill_id` itself must be a UUID distinct from every resolver value. A missing approved TDD, logical display name in a platform inventory, unexpected TDD, legacy bootstrap/v11, duplicate, or any other extra Skill fails closed. It also verifies config hash, manifest hash, shared 0700 daemon root and the fixed 000 receipt. A failed or drifting preflight persists no run and returns zero tasks. The preflight token is bound to the root, manifest and all claims; each envelope must carry it; task-time claims repeat the same inventory check.

Question-SQL requires both 170 and 180 receipts before 110 may enter the data stage. Event data and ORM lanes are independent after 230 but both are required at 260. Foundation uses its separate 210→220→241→242→260→210→010 chain and forbids 230/251/252. Every failure is mapped to its affected upstream restart node; attempts are limited to three, duplicate key replays add no tasks, unknown/duplicate/drifting receipts fail closed, and terminal completion has no successors.
