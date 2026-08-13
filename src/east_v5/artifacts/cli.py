"""Machine CLI for local runtime consumers; it never performs formal release."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from .registry import ArtifactRegistry

def read_json(path: str): return json.loads(Path(path).read_text(encoding="utf-8"))

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["register", "resolve", "lineage", "verify", "transition", "validate-locator", "migrate-locator", "audit"]); parser.add_argument("--repo-root", required=True); parser.add_argument("--roots-json", required=True); parser.add_argument("--issue-id", required=True); parser.add_argument("--run-id", required=True); parser.add_argument("--attempt", type=int, required=True); parser.add_argument("--ref-json"); parser.add_argument("--package-json"); parser.add_argument("--target-status"); parser.add_argument("--locator")
    args = parser.parse_args(); registry = ArtifactRegistry(Path(args.repo_root), read_json(args.roots_json), args.issue_id, args.run_id, args.attempt)
    ref = read_json(args.ref_json) if args.ref_json else None
    if args.command == "register":
        package = read_json(args.package_json); result = registry.register(package["envelope"], package["payload"])
    elif args.command == "resolve": result = registry.resolve(ref)
    elif args.command == "lineage": result = registry.lineage(ref)
    elif args.command == "verify": result = registry.resolve(ref)["envelope"]
    elif args.command == "transition": result = registry.transition(ref, args.target_status)
    elif args.command == "validate-locator": result = {"storage_locator": registry.validate_locator(args.locator)}
    elif args.command == "migrate-locator": result = registry.migrate_locator(ref, args.locator)
    else: result = registry.audit(ref)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
