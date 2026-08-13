from __future__ import annotations

from pathlib import Path

from east_v5.governance import ContractError, attempt_path, load_json, verify_governed_manifest


def consume_eas15_stub(repo_root: Path, roots: dict[str, object], issue_id: str, run_id: str, attempt: int) -> Path:
    """Minimal safe consumer: validate all governance data before one runtime write."""
    manifest = verify_governed_manifest(repo_root)
    required = {"contracts/common/", "src/east_v5/artifacts/", "tests/artifacts/", "runtime artifact registry locator"}
    downstream_path = repo_root / manifest["downstream_contract"]
    downstream = load_json(downstream_path)
    if downstream.get("schema_version") != "v5.downstream-contract/v1" or not required.issubset(set(downstream.get("required_locators", []))):
        raise ContractError("DOWNSTREAM_CONTRACT_INCOMPLETE")
    for locator in ("contracts/common/", "src/east_v5/artifacts/", "tests/artifacts/"):
        if not (repo_root / locator).is_dir():
            raise ContractError("LOCATOR_INVALID")
    target = attempt_path(roots, issue_id, run_id, attempt) / "COMMON-ENVELOPE.placeholder"
    if target.exists() and target.read_text(encoding="utf-8") != "development placeholder\n":
        raise ContractError("IDENTITY_CONTENT_CONFLICT")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("development placeholder\n", encoding="utf-8")
    return target
