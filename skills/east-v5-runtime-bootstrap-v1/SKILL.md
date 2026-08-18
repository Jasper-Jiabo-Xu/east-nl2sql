---
name: east-v5-runtime-bootstrap-v1
description: Run the fail-closed EAST V5 runtime bootstrap claim for agents 010, 110, or 120. Use only for bootstrap-only preflight tasks that verify the immutable Skill bundle, task envelope, candidate provenance, adapter hash, and root binding before any business artifact, receipt, or downstream task exists.
---

# EAST V5 Runtime Bootstrap V1

Run the supporting entrypoint directly; do not use an Agent instruction, checkout, network fetch, project local directory, or business payload as a substitute.

```text
python3 scripts/skill_bundle_runner.py claim-preflight \
  --envelope-file <controlled-envelope.json> \
  --claim-file <controlled-claim.json>
```

Use only a claim whose target UUID is 010, 110, or 120, whose provider matches the frozen manifest, and whose enabled Skill IDs include this immutable Skill. Treat a nonzero exit as fail-closed: create no artifact, receipt, downstream task, or retry task.

The result is a redacted bootstrap claim. It may be used to compare the three real task runtimes, but it is not a business execution receipt and does not establish a sequencing edge.
