"""Agent 120: 监管处罚不可丢失事实构造agent.

Extracts non-lossable facts from frozen penalty source packages and
enriches them with traceable external evidence.
"""
from __future__ import annotations
from east_v5.agents.east_120.extractor import (
    FactExtractor,
    FACT_TYPES,
    MUST_PRESERVE_VALUES,
    extract_facts,
    build_fact_package,
    validate_source_package,
    validate_fact_package,
)

__all__ = [
    "FactExtractor",
    "FACT_TYPES",
    "MUST_PRESERVE_VALUES",
    "extract_facts",
    "build_fact_package",
    "validate_source_package",
    "validate_fact_package",
]
