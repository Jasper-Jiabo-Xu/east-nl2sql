"""Production-only construction of the Foundation 241/242 runtime pair."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from east_v5.agents.foundation_contract import FoundationInvocationVerifier


@dataclass(frozen=True)
class FoundationRuntimeAssembly:
    """The only supported production injection route for Foundation evidence."""

    invocation_service: FoundationInvocationVerifier

    def generator(self, repo_root: Path) -> Any:
        return importlib.import_module("east_v5.agents.241.generator").BoundDataGenerator(
            repo_root, foundation_invocation_verifier=self.invocation_service,
        )

    def validator(self, repo_root: Path) -> Any:
        return importlib.import_module("east_v5.agents.242.validator").DataValidator(
            repo_root, foundation_invocation_verifier=self.invocation_service,
        )

    @classmethod
    def from_runtime_adapter(cls, adapter: Any, *, task_id: str, runtime_id: str) -> "FoundationRuntimeAssembly":
        return cls(adapter.foundation_invocation_service(task_id=task_id, runtime_id=runtime_id))
