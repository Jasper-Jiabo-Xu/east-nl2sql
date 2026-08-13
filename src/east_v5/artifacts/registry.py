from __future__ import annotations

from pathlib import Path

from east_v5.governance import ContractError, attempt_path, verify_governed_manifest


def consume_eas15_stub(repo_root: Path, roots: dict[str, object], issue_id: str, run_id: str, attempt: int) -> Path:
    """Minimal safe consumer: validate all governance data before one runtime write."""
    manifest = verify_governed_manifest(repo_root)
    required = {"contracts/common/", "src/east_v5/artifacts/", "tests/artifacts/", "runtime artifact registry locator"}
    downstream = (repo_root / manifest["downstream_contract"]).read_text(encoding="utf-8")
    if not all(item in downstream for item in required):
        raise ContractError("DOWNSTREAM_CONTRACT_INCOMPLETE")
    target = attempt_path(roots, issue_id, run_id, attempt) / "COMMON-ENVELOPE.placeholder"
    if target.exists() and target.read_text(encoding="utf-8") != "development placeholder\n":
        raise ContractError("IDENTITY_CONTENT_CONFLICT")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("development placeholder\n", encoding="utf-8")
    return target
