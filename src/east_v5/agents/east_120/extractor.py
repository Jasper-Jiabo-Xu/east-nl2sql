"""Hard-code fact extractor for Agent 120: 监管处罚不可丢失事实构造agent.

Extracts non-lossable penalty facts from frozen source packages and
builds PENALTY-FACT-PACKAGE payloads.  The LLM augments facts and
external evidence at runtime; this module provides deterministic
extraction, hard-code validation and quote-hash verification.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.governance import ContractError, load_json

SCHEMA_VERSION_SOURCE = "penalty-source-v1"
SCHEMA_VERSION_FACT = "penalty-fact-v1"

FACT_TYPES = frozenset({
    "subject", "behavior", "object", "time", "amount",
    "condition", "result", "regulatory_conclusion", "unknown",
})
MUST_PRESERVE_VALUES = frozenset({"yes", "no", "conditional"})
JOIN_STATUSES = frozenset({"matched", "list_only", "text_only", "duplicate", "conflict"})
FULL_TEXT_STATUSES = frozenset({"available", "missing_record", "empty_content"})

_SPAN_ID = re.compile(r"^span-[0-9]{4,}$")
_FACT_ID = re.compile(r"^fact-[0-9]{3}$")

# Fields in PENALTY-SOURCE-PACKAGE that map to deterministic source facts.
# Each entry: (raw_field, fact_type, predicate, must_preserve)
_FIELD_FACT_MAP: list[tuple[str, str, str, str]] = [
    ("punished_person_name_raw", "subject", "被处罚个人", "yes"),
    ("punished_org_name_raw", "subject", "被处罚单位", "yes"),
    ("legal_representative_name_raw", "subject", "法定代表人", "conditional"),
    ("violation_facts_raw", "behavior", "违法违规事实", "yes"),
    ("penalty_basis_raw", "regulatory_conclusion", "行政处罚依据", "yes"),
    ("penalty_decision_raw", "result", "行政处罚决定", "yes"),
    ("decision_authority_raw", "regulatory_conclusion", "作出处罚决定机关", "conditional"),
    ("decision_date_raw", "time", "作出处罚决定日期", "yes"),
    ("decision_document_number_raw", "regulatory_conclusion", "行政处罚决定书文号", "yes"),
]


def _fail(code: str) -> None:
    raise ContractError(code)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span_index(source_spans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a validated span-id → span map."""
    index: dict[str, dict[str, Any]] = {}
    for span in source_spans:
        sid = span.get("source_span_id")
        if sid is None:
            _fail("SOURCE_SPAN_MISSING_ID")
        if not isinstance(sid, str) or not _SPAN_ID.fullmatch(sid):
            _fail("SOURCE_SPAN_ID_INVALID")
        if sid in index:
            _fail("SOURCE_SPAN_DUPLICATE_ID")
        index[sid] = span
    return index


def _resolve_span_text(span: dict[str, Any], full_text: str | None) -> str:
    """Resolve the text of a span: prefer span's own text, then slice full_text."""
    if "text" in span and isinstance(span["text"], str):
        return span["text"]
    if full_text is not None and 0 <= span["char_start"] < span["char_end"] <= len(full_text):
        return full_text[span["char_start"]:span["char_end"]]
    _fail("SOURCE_SPAN_TEXT_UNRESOLVABLE")


def _validate_source_schema(repo_root: Path, payload: dict[str, Any]) -> None:
    schema = load_json(
        repo_root / "contracts" / "packages" / "penalty-source-package.schema.json"
    )
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        raise ContractError("SCHEMA_VALIDATION_FAILED:PENALTY_SOURCE_PACKAGE") from exc


def _validate_fact_schema(repo_root: Path, payload: dict[str, Any]) -> None:
    schema = load_json(
        repo_root / "contracts" / "packages" / "penalty-fact-package.schema.json"
    )
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        raise ContractError("SCHEMA_VALIDATION_FAILED:PENALTY_FACT_PACKAGE") from exc


# --------------------------------------------------------------------------- #
#  Public validation entry points                                              #
# --------------------------------------------------------------------------- #

def validate_source_package(repo_root: Path, payload: dict[str, Any]) -> None:
    """Full validation of a PENALTY-SOURCE-PACKAGE: schema + hard rules."""
    if not isinstance(payload, dict):
        _fail("PAYLOAD_NOT_OBJECT")

    # Version check BEFORE schema validation so we get a precise error code.
    if payload.get("source_package_schema_version") != SCHEMA_VERSION_SOURCE:
        _fail("SCHEMA_VERSION_UNSUPPORTED:PENALTY_SOURCE")

    _validate_source_schema(repo_root, payload)

    # Span integrity
    spans = payload.get("source_spans", [])
    _span_index(spans)

    # text_source_ref must be null when join_status is list_only
    if payload.get("join_status") == "list_only" and payload.get("text_source_ref") is not None:
        _fail("LIST_ONLY_HAS_TEXT_REF")

    # full_text_raw must be null/empty when status is not available
    ft_status = payload.get("full_text_status")
    if ft_status in ("missing_record", "empty_content"):
        if payload.get("full_text_raw"):
            _fail("FULL_TEXT_CONTENT_INCONSISTENT")


def validate_fact_package(repo_root: Path, payload: dict[str, Any]) -> None:
    """Full validation of a PENALTY-FACT-PACKAGE: schema + hard rules."""
    if not isinstance(payload, dict):
        _fail("PAYLOAD_NOT_OBJECT")

    # Version check BEFORE schema validation so we get a precise error code.
    if payload.get("penalty_fact_package_schema_version") != SCHEMA_VERSION_FACT:
        _fail("SCHEMA_VERSION_UNSUPPORTED:PENALTY_FACT")

    _validate_fact_schema(repo_root, payload)

    # Duplicate fact IDs
    seen: set[str] = set()
    for fact in payload.get("source_facts", []):
        fid = fact.get("penalty_fact_id")
        if fid in seen:
            _fail("FACT_ID_DUPLICATE")
        seen.add(fid)
        if fact.get("fact_type") not in FACT_TYPES:
            _fail("FACT_TYPE_INVALID")
        if fact.get("must_preserve_in_question") not in MUST_PRESERVE_VALUES:
            _fail("MUST_PRESERVE_INVALID")

    # Evidence refs must have non-empty snippet
    ee = payload.get("external_evidence", {})
    for section in ("penalty_intent", "business_meaning", "penalty_background"):
        for ref in ee.get(section, {}).get("evidence_refs", []):
            if not ref.get("snippet"):
                _fail("EVIDENCE_SNIPPET_EMPTY")
    for rule in ee.get("regulatory_rules", []):
        for ref in rule.get("evidence_refs", []):
            if not ref.get("snippet"):
                _fail("EVIDENCE_SNIPPET_EMPTY")


# --------------------------------------------------------------------------- #
#  Deterministic extraction                                                    #
# --------------------------------------------------------------------------- #

def extract_facts(source_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract deterministic source_facts from structured raw fields.

    The LLM augments these at runtime; this function provides the
    hard-code baseline guaranteeing no structured field is missed.
    """
    spans = source_payload.get("source_spans", [])
    full_text = source_payload.get("full_text_raw")
    facts: list[dict[str, Any]] = []
    seq = 1

    def _next_id() -> str:
        nonlocal seq
        fid = f"fact-{seq:03d}"
        seq += 1
        return fid

    def _find_span_refs(raw_text: str) -> list[str]:
        """Return span_ids whose text overlaps with the raw field value."""
        if not raw_text or not spans:
            return []
        matches: list[str] = []
        for span in spans:
            span_text = _resolve_span_text(span, full_text)
            if span_text and raw_text and (raw_text in span_text or span_text in raw_text):
                sid = span["source_span_id"]
                if sid not in matches:
                    matches.append(sid)
        return matches

    for raw_field, fact_type, predicate, must_preserve in _FIELD_FACT_MAP:
        raw_value = source_payload.get(raw_field)
        if not raw_value or not isinstance(raw_value, str):
            continue
        facts.append({
            "penalty_fact_id": _next_id(),
            "fact_type": fact_type,
            "structured_fact": {
                "subject": (
                    source_payload.get("punished_org_name_raw")
                    or source_payload.get("punished_person_name_raw")
                    or "未知主体"
                ),
                "predicate": predicate,
                "object": raw_value,
                "qualifier": None,
                "value": None,
            },
            "original_text": raw_value,
            "source_span_refs": _find_span_refs(raw_value),
            "must_preserve_in_question": must_preserve,
        })

    return facts


def _derive_uncertainties(source_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Automatically derive uncertainties from gaps in the source package."""
    result: list[dict[str, Any]] = []
    if not source_payload.get("punished_person_name_raw") and not source_payload.get("punished_org_name_raw"):
        result.append({
            "type": "missing_info",
            "description": "被处罚主体（个人和单位）均缺失",
            "needs_human_review": True,
        })
    ft_status = source_payload.get("full_text_status")
    if ft_status in ("missing_record", "empty_content"):
        result.append({
            "type": "missing_info",
            "description": f"处罚全文状态为 {ft_status}，无法从全文提取补充事实",
            "needs_human_review": False,
        })
    if source_payload.get("join_status") in ("duplicate", "conflict"):
        result.append({
            "type": "ambiguity",
            "description": f"来源关联状态为 {source_payload.get('join_status')}，存在多记录或字段冲突",
            "needs_human_review": True,
        })
    return result


def build_fact_package(
    source_payload: dict[str, Any],
    *,
    llm_facts: list[dict[str, Any]] | None = None,
    external_evidence: dict[str, Any] | None = None,
    evidence_conflicts: list[dict[str, Any]] | None = None,
    uncertainties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a PENALTY-FACT-PACKAGE from a source package.

    Deterministic facts from extract_facts are merged with optional
    LLM-provided facts.  External evidence and uncertainties are
    provided by the caller (LLM at runtime, stub in tests).
    """
    deterministic = extract_facts(source_payload)
    span_index = _span_index(source_payload.get("source_spans", []))

    all_facts: list[dict[str, Any]] = list(deterministic)
    if llm_facts:
        existing_texts = {f["original_text"] for f in deterministic}
        for fact in llm_facts:
            for ref in fact.get("source_span_refs", []):
                if ref not in span_index:
                    _fail("SPAN_REF_NOT_FOUND")
            if fact["original_text"] not in existing_texts:
                all_facts.append(fact)
                existing_texts.add(fact["original_text"])

    # Renumber sequentially
    for i, fact in enumerate(all_facts, 1):
        fact["penalty_fact_id"] = f"fact-{i:03d}"

    default_evidence = {
        "penalty_intent": {"description": "待LLM补充", "evidence_refs": []},
        "regulatory_rules": [],
        "business_meaning": {"description": "待LLM补充", "evidence_refs": []},
        "penalty_background": {"description": "待LLM补充", "evidence_refs": []},
    }

    return {
        "source_facts": all_facts,
        "external_evidence": external_evidence or default_evidence,
        "evidence_conflicts": evidence_conflicts or [],
        "uncertainties": uncertainties or _derive_uncertainties(source_payload),
        "penalty_fact_package_schema_version": SCHEMA_VERSION_FACT,
    }


# --------------------------------------------------------------------------- #
#  Stateful extractor with review feedback support                             #
# --------------------------------------------------------------------------- #

class FactExtractor:
    """Stateful fact extractor that validates input/output and supports
    review-driven re-extraction.

    Usage::

        extractor = FactExtractor(repo_root)
        extractor.validate_input(source_payload)
        fact_payload = extractor.extract(source_payload)
        extractor.validate_output(fact_payload, source_payload)
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def validate_input(self, source_payload: dict[str, Any]) -> None:
        validate_source_package(self.repo_root, source_payload)

    def extract(self, source_payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return build_fact_package(source_payload, **kwargs)

    def validate_output(
        self,
        fact_payload: dict[str, Any],
        source_payload: dict[str, Any],
    ) -> None:
        validate_fact_package(self.repo_root, fact_payload)

        # Cross-validate span refs
        span_ids = {s["source_span_id"] for s in source_payload.get("source_spans", [])}
        for fact in fact_payload.get("source_facts", []):
            for ref in fact.get("source_span_refs", []):
                if ref not in span_ids:
                    _fail("SPAN_REF_NOT_IN_SOURCE")

            # Quote hash check: original_text must derive from a span or raw field
            if source_payload.get("full_text_status") == "available":
                full_text = source_payload.get("full_text_raw") or ""
                span_texts = [
                    _resolve_span_text(s, full_text)
                    for s in source_payload.get("source_spans", [])
                ]
                original = fact["original_text"]
                if not any(original in st or st in original for st in span_texts if st):
                    raw_values = [
                        source_payload.get(f)
                        for f, _, _, _ in _FIELD_FACT_MAP
                    ]
                    if original not in [v for v in raw_values if v]:
                        _fail("QUOTE_HASH_MISMATCH")

    def re_extract_from_review(
        self,
        source_payload: dict[str, Any],
        review_report: dict[str, Any],
        previous_fact_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-extract facts after a 170/180 review identifies omissions."""
        self.validate_input(source_payload)
        new_payload = self.extract(source_payload)

        # Preserve previous external evidence
        if previous_fact_payload.get("external_evidence"):
            new_payload["external_evidence"] = previous_fact_payload["external_evidence"]

        # Merge previous non-deterministic facts
        det_texts = {f["original_text"] for f in new_payload["source_facts"]}
        for prev_fact in previous_fact_payload.get("source_facts", []):
            if prev_fact["original_text"] not in det_texts:
                new_payload["source_facts"].append(prev_fact)
                det_texts.add(prev_fact["original_text"])

        # Apply review-suggested additions
        for err in review_report.get("error_details", []):
            suggestion = err.get("suggested_fact")
            if suggestion and suggestion.get("original_text") not in det_texts:
                new_payload["source_facts"].append(suggestion)
                det_texts.add(suggestion["original_text"])

        # Renumber
        for i, fact in enumerate(new_payload["source_facts"], 1):
            fact["penalty_fact_id"] = f"fact-{i:03d}"

        new_payload["uncertainties"].append({
            "type": "other",
            "description": "基于审核反馈重新提取的事实包",
            "needs_human_review": False,
        })

        self.validate_output(new_payload, source_payload)
        return new_payload
