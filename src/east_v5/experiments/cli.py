from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from east_v5.experiments.s0_harness import HarnessError, consume_s0_manifest, load_json, run_harness, validate_dataset_manifest, validate_experiment_contract, validate_run_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="EAST V5 Stage 1 S0 baseline harness.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate S0 experiment, dataset, and baseline manifests.")
    validate_parser.add_argument("--experiment-contract", required=True)
    validate_parser.add_argument("--dataset-manifest", required=True)
    validate_parser.add_argument("--baseline-run-manifest", required=True)

    run_parser = subparsers.add_parser("run-smoke", help="Run six-baseline synthetic S0 smoke.")
    run_parser.add_argument("--experiment-contract", required=True)
    run_parser.add_argument("--dataset-manifest", required=True)
    run_parser.add_argument("--baseline-run-manifest", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--native-worktree-root")
    run_parser.add_argument("--mode", choices=["concurrent", "serial"], default="concurrent")

    consume_parser = subparsers.add_parser("consume-s0", help="Validate a frozen S0 dataset manifest without calling a model.")
    consume_parser.add_argument("--dataset-manifest", required=True)
    consume_parser.add_argument("--evidence-receipt")

    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate_experiment_contract(load_json(Path(args.experiment_contract)))
            validate_dataset_manifest(load_json(Path(args.dataset_manifest)))
            validate_run_manifest(load_json(Path(args.baseline_run_manifest)))
            print(json.dumps({"status": "ok"}, ensure_ascii=False, sort_keys=True))
        elif args.command == "run-smoke":
            summary = run_harness(Path(args.experiment_contract), Path(args.dataset_manifest), Path(args.baseline_run_manifest), Path(args.output_dir), args.mode, Path(args.native_worktree_root) if args.native_worktree_root else None)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        elif args.command == "consume-s0":
            receipt = Path(args.evidence_receipt) if args.evidence_receipt else None
            summary = consume_s0_manifest(Path(args.dataset_manifest), receipt)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except HarnessError as exc:
        print(json.dumps({"status": "failed", "failure_code": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
