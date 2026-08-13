"""Narrow CLI for local development/runtime consumers, never formal release."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from .registry import ArtifactRegistry

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["resolve", "lineage"]); parser.add_argument("--repo-root", required=True); parser.add_argument("--roots-json", required=True); parser.add_argument("--issue-id", required=True); parser.add_argument("--run-id", required=True); parser.add_argument("--attempt", type=int, required=True); parser.add_argument("--ref-json", required=True)
    args = parser.parse_args(); registry = ArtifactRegistry(Path(args.repo_root), json.loads(Path(args.roots_json).read_text()), args.issue_id, args.run_id, args.attempt); ref = json.loads(Path(args.ref_json).read_text())
    print(json.dumps(registry.resolve(ref) if args.command == "resolve" else registry.lineage(ref), ensure_ascii=False, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
