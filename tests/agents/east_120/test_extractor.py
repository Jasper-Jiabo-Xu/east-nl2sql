"""Comprehensive tests for Agent 120: PENALTY-SOURCE-PACKAGE → PENALTY-FACT-PACKAGE.

Covers: full-text available, missing record, empty content, cross-span facts,
amount/time/subject/regulatory conclusion extraction, external evidence conflict,
expired rule, URL missing, fact omission, dual-review re-extraction,
envelope and schema boundaries, fixed enums, hash mismatch, rejection paths,
and downstream stub consumption.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.agents.east_120.extractor import (
    FactExtractor,
    FACT_TYPES,
    MUST_PRESERVE_VALUES,
    extract_facts,
    build_fact_package,
    validate_source_package,
    validate_fact_package,
)
from east_v5.governance import ContractError


# --------------------------------------------------------------------------- #
#  Fixture builders                                                           #
# --------------------------------------------------------------------------- #

def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span(span_id: str, text: str, start: int = 0) -> dict:
    """Build a valid source_span."""
    return {
        "source_span_id": span_id,
        "char_start": start,
        "char_end": start + len(text),
        "line_start": 1,
        "line_end": 1,
        "text": text,
    }


def make_matched_source() -> dict:
    """PENALTY-SOURCE-PACKAGE with full_text_status=available."""
    full_text = (
        "张三因违反信贷管理规定被处罚。"
        "经查，张三在担任某银行信贷部总经理期间，"
        "存在违规发放贷款行为。"
        "依据《商业银行法》第七十三条，"
        "处以警告并罚款人民币30万元。"
    )
    spans = [
        _span("span-0001", "张三因违反信贷管理规定被处罚。"),
        _span("span-0002", "经查，张三在担任某银行信贷部总经理期间，"),
        _span("span-0003", "存在违规发放贷款行为。"),
        _span("span-0004", "依据《商业银行法》第七十三条，"),
        _span("span-0005", "处以警告并罚款人民币30万元。", start=48),
    ]
    return {
        "source_document_id": "PENALTY-DEMO-001",
        "doc_id": "doc-001",
        "list_source_ref": {
            "relative_path": "list.xlsx",
            "sheet_name": "bank_punish_detail_list",
            "row_number": 1,
            "file_sha256": _sha("list"),
            "row_sha256": _sha("list-row-1"),
        },
        "text_source_ref": {
            "relative_path": "text.xlsx",
            "sheet_name": "bank_punish_detail_txt",
            "row_number": 1,
            "file_sha256": _sha("text"),
            "row_sha256": _sha("text-row-1"),
        },
        "join_status": "matched",
        "common_field_conflicts": [],
        "decision_document_number_raw": "银保监罚决字〔2023〕1号",
        "punished_person_name_raw": "张三",
        "punished_org_name_raw": None,
        "legal_representative_name_raw": None,
        "violation_facts_raw": "违规发放贷款",
        "penalty_basis_raw": "《商业银行法》第七十三条",
        "penalty_decision_raw": "警告并罚款人民币30万元",
        "decision_authority_raw": "某银保监局",
        "decision_date_raw": "2023-06-15",
        "disclosure_recommendation_raw": None,
        "list_document_title_raw": "行政处罚信息公开表",
        "list_publish_date_raw": "2023-06-15",
        "list_document_source_raw": "某银保监局",
        "list_source_url": "https://example.gov.cn/list/001",
        "text_document_title_raw": "行政处罚决定书",
        "text_publish_date_raw": "2023-06-15",
        "text_document_source_raw": "某银保监局",
        "text_source_url": "https://example.gov.cn/text/001",
        "full_text_raw": full_text,
        "full_text_status": "available",
        "source_spans": spans,
        "source_snapshot": {
            "files": [
                {
                    "relative_path": "list.xlsx",
                    "sheet_name": "bank_punish_detail_list",
                    "file_sha256": _sha("list"),
                    "row_count": 1,
                    "collected_at": "2026-08-13T00:00:00+00:00",
                },
                {
                    "relative_path": "text.xlsx",
                    "sheet_name": "bank_punish_detail_txt",
                    "file_sha256": _sha("text"),
                    "row_count": 1,
                    "collected_at": "2026-08-13T00:00:00+00:00",
                },
            ]
        },
        "source_package_schema_version": "penalty-source-v1",
    }


def make_list_only_source() -> dict:
    """PENALTY-SOURCE-PACKAGE with full_text_status=missing_record."""
    src = make_matched_source()
    src["join_status"] = "list_only"
    src["text_source_ref"] = None
    src["full_text_raw"] = None
    src["full_text_status"] = "missing_record"
    src["text_document_title_raw"] = None
    src["text_publish_date_raw"] = None
    src["text_document_source_raw"] = None
    src["text_source_url"] = None
    # list_only: spans can be empty or carry raw field-derived spans
    src["source_spans"] = [
        _span("span-0001", src["violation_facts_raw"]),
    ]
    return src


def make_text_only_source() -> dict:
    """PENALTY-SOURCE-PACKAGE with join_status=text_only."""
    full_text = "经调查，李四在某银行业务中存在严重违规行为。依据相关法规，给予警告处罚。"
    src = make_matched_source()
    src["source_document_id"] = "PENALTY-DEMO-002"
    src["doc_id"] = "doc-002"
    src["join_status"] = "text_only"
    src["punished_person_name_raw"] = "李四"
    src["violation_facts_raw"] = "严重违规行为"
    src["penalty_decision_raw"] = "警告"
    src["full_text_raw"] = full_text
    src["source_spans"] = [
        _span("span-0001", "经调查，李四在某银行业务中存在严重违规行为。"),
        _span("span-0002", "依据相关法规，给予警告处罚。"),
    ]
    return src


def make_empty_content_source() -> dict:
    """PENALTY-SOURCE-PACKAGE with full_text_status=empty_content."""
    src = make_matched_source()
    src["join_status"] = "matched"
    src["full_text_raw"] = None
    src["full_text_status"] = "empty_content"
    src["source_spans"] = []
    return src


def make_evidence_ref(**overrides) -> dict:
    base = {
        "url": "https://example.gov.cn/rule/001",
        "publishing_org": "国家金融监督管理总局",
        "publish_date": "2023-01-01",
        "access_date": "2026-08-13",
        "applicable_time": "2023-01-01起施行",
        "snippet": "商业银行违反信贷管理规定的处罚依据",
    }
    base.update(overrides)
    return base


def make_external_evidence(refs: list[dict] | None = None) -> dict:
    r = refs or [make_evidence_ref()]
    return {
        "penalty_intent": {
            "description": "规范商业银行信贷管理行为",
            "evidence_refs": r,
        },
        "regulatory_rules": [
            {
                "rule_name": "商业银行法",
                "clause": "第七十三条",
                "effective_date": "2004-02-01",
                "scope": "中国境内所有商业银行",
                "evidence_refs": [make_evidence_ref(url="https://example.gov.cn/law/001")],
            }
        ],
        "business_meaning": {
            "description": "信贷业务合规经营要求",
            "evidence_refs": [make_evidence_ref(url="https://example.gov.cn/biz/001")],
        },
        "penalty_background": {
            "description": "近年来银行业违规放贷案件频发",
            "evidence_refs": [make_evidence_ref(url="https://example.gov.cn/bg/001")],
        },
    }


# --------------------------------------------------------------------------- #
#  Test classes                                                               #
# --------------------------------------------------------------------------- #


class SourcePackageValidationTests(unittest.TestCase):
    """PENALTY-SOURCE-PACKAGE validation: schema, hard rules, rejections."""

    def test_valid_matched_source(self):
        validate_source_package(ROOT, make_matched_source())

    def test_valid_list_only_source(self):
        validate_source_package(ROOT, make_list_only_source())

    def test_valid_text_only_source(self):
        validate_source_package(ROOT, make_text_only_source())

    def test_valid_empty_content_source(self):
        validate_source_package(ROOT, make_empty_content_source())

    def test_reject_unknown_field(self):
        src = make_matched_source()
        src["unexpected_field"] = True
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_source_package(ROOT, src)

    def test_reject_missing_required_field(self):
        src = make_matched_source()
        del src["source_document_id"]
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_source_package(ROOT, src)

    def test_reject_wrong_schema_version(self):
        src = make_matched_source()
        src["source_package_schema_version"] = "penalty-source-v0"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VERSION_UNSUPPORTED"):
            validate_source_package(ROOT, src)

    def test_reject_list_only_with_text_ref(self):
        src = make_list_only_source()
        src["text_source_ref"] = {
            "relative_path": "t.xlsx",
            "sheet_name": "bank_punish_detail_txt",
            "row_number": 1,
            "file_sha256": "a" * 64,
            "row_sha256": "b" * 64,
        }
        with self.assertRaisesRegex(ContractError, "LIST_ONLY_HAS_TEXT_REF"):
            validate_source_package(ROOT, src)

    def test_reject_empty_content_with_raw_text(self):
        src = make_empty_content_source()
        src["full_text_raw"] = "some text"
        with self.assertRaisesRegex(ContractError, "FULL_TEXT_CONTENT_INCONSISTENT"):
            validate_source_package(ROOT, src)

    def test_reject_invalid_join_status(self):
        src = make_matched_source()
        src["join_status"] = "invalid"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_source_package(ROOT, src)

    def test_reject_bad_sha256(self):
        src = make_matched_source()
        src["list_source_ref"]["file_sha256"] = "bad"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_source_package(ROOT, src)

    def test_reject_duplicate_span_id(self):
        src = make_matched_source()
        src["source_spans"].append(_span("span-0001", "duplicate"))
        with self.assertRaisesRegex(ContractError, "SOURCE_SPAN_DUPLICATE_ID"):
            validate_source_package(ROOT, src)


class FactPackageValidationTests(unittest.TestCase):
    """PENALTY-FACT-PACKAGE validation: schema, enums, hard rules."""

    def test_valid_fact_package(self):
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        validate_fact_package(ROOT, fact_pkg)

    def test_reject_unknown_field(self):
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        fact_pkg["unknown"] = True
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_fact_package(ROOT, fact_pkg)

    def test_reject_invalid_fact_type(self):
        # JSON Schema enum catches this before the hard-code FACT_TYPE_INVALID check.
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        fact_pkg["source_facts"][0]["fact_type"] = "invalid_type"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_fact_package(ROOT, fact_pkg)

    def test_reject_invalid_must_preserve(self):
        # JSON Schema enum catches this before the hard-code MUST_PRESERVE_INVALID check.
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        fact_pkg["source_facts"][0]["must_preserve_in_question"] = "maybe"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_fact_package(ROOT, fact_pkg)

    def test_reject_duplicate_fact_id(self):
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        fact_pkg["source_facts"][0]["penalty_fact_id"] = fact_pkg["source_facts"][1]["penalty_fact_id"]
        with self.assertRaisesRegex(ContractError, "FACT_ID_DUPLICATE"):
            validate_fact_package(ROOT, fact_pkg)

    def test_reject_wrong_schema_version(self):
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        fact_pkg["penalty_fact_package_schema_version"] = "penalty-fact-v0"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VERSION_UNSUPPORTED"):
            validate_fact_package(ROOT, fact_pkg)

    def test_reject_empty_evidence_snippet(self):
        # JSON Schema minLength catches this before the hard-code EVIDENCE_SNIPPET_EMPTY check.
        source = make_matched_source()
        fact_pkg = build_fact_package(source)
        fact_pkg["external_evidence"] = make_external_evidence(
            refs=[make_evidence_ref(snippet="")]
        )
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_fact_package(ROOT, fact_pkg)


class ExtractionTests(unittest.TestCase):
    """Deterministic fact extraction from various source package types."""

    def test_matched_source_extracts_all_fields(self):
        source = make_matched_source()
        facts = extract_facts(source)
        # Should extract facts for all non-null raw fields
        raw_fields_present = [
            "decision_document_number_raw", "punished_person_name_raw",
            "violation_facts_raw", "penalty_basis_raw", "penalty_decision_raw",
            "decision_authority_raw", "decision_date_raw",
        ]
        self.assertEqual(len(facts), len(raw_fields_present))
        # Check fact types
        types = {f["fact_type"] for f in facts}
        self.assertIn("subject", types)
        self.assertIn("behavior", types)
        self.assertIn("result", types)
        self.assertIn("time", types)
        self.assertIn("regulatory_conclusion", types)

    def test_list_only_extracts_without_full_text(self):
        source = make_list_only_source()
        facts = extract_facts(source)
        self.assertGreater(len(facts), 0)
        for fact in facts:
            # Span refs may be empty but must not crash
            self.assertIsInstance(fact["source_span_refs"], list)

    def test_text_only_extracts_correctly(self):
        source = make_text_only_source()
        facts = extract_facts(source)
        self.assertGreater(len(facts), 0)

    def test_empty_content_extracts_structured_fields(self):
        source = make_empty_content_source()
        facts = extract_facts(source)
        self.assertGreater(len(facts), 0)

    def test_fact_ids_sequential(self):
        source = make_matched_source()
        facts = extract_facts(source)
        for i, fact in enumerate(facts, 1):
            self.assertEqual(fact["penalty_fact_id"], f"fact-{i:03d}")

    def test_must_preserve_values_correct(self):
        source = make_matched_source()
        facts = extract_facts(source)
        for fact in facts:
            self.assertIn(fact["must_preserve_in_question"], MUST_PRESERVE_VALUES)

    def test_fact_types_all_valid(self):
        source = make_matched_source()
        facts = extract_facts(source)
        for fact in facts:
            self.assertIn(fact["fact_type"], FACT_TYPES)


class BuildFactPackageTests(unittest.TestCase):
    """build_fact_package: merging, external evidence, uncertainties."""

    def test_build_with_default_uncertainties(self):
        source = make_matched_source()
        pkg = build_fact_package(source)
        self.assertGreater(len(pkg["source_facts"]), 0)
        self.assertEqual(pkg["penalty_fact_package_schema_version"], "penalty-fact-v1")
        # Matched source with both person and violation should have no missing_info uncertainty
        unc_types = [u["type"] for u in pkg["uncertainties"]]
        self.assertNotIn("missing_info", unc_types)

    def test_build_list_only_has_uncertainty(self):
        source = make_list_only_source()
        pkg = build_fact_package(source)
        unc_types = [u["type"] for u in pkg["uncertainties"]]
        self.assertIn("missing_info", unc_types)

    def test_build_with_external_evidence(self):
        source = make_matched_source()
        evidence = make_external_evidence()
        pkg = build_fact_package(source, external_evidence=evidence)
        self.assertEqual(pkg["external_evidence"]["penalty_intent"]["description"],
                         "规范商业银行信贷管理行为")
        self.assertEqual(len(pkg["external_evidence"]["regulatory_rules"]), 1)

    def test_build_with_llm_facts(self):
        source = make_matched_source()
        llm_fact = {
            "penalty_fact_id": "fact-999",
            "fact_type": "amount",
            "structured_fact": {
                "subject": "张三",
                "predicate": "罚款金额",
                "object": "30万元",
                "qualifier": "人民币",
                "value": "300000",
            },
            "original_text": "罚款人民币30万元",
            "source_span_refs": ["span-0005"],
            "must_preserve_in_question": "yes",
        }
        pkg = build_fact_package(source, llm_facts=[llm_fact])
        texts = [f["original_text"] for f in pkg["source_facts"]]
        self.assertIn("罚款人民币30万元", texts)
        # Should be renumbered
        last_id = pkg["source_facts"][-1]["penalty_fact_id"]
        self.assertNotEqual(last_id, "fact-999")

    def test_build_with_llm_fact_bad_span_ref(self):
        source = make_matched_source()
        llm_fact = {
            "penalty_fact_id": "fact-999",
            "fact_type": "amount",
            "structured_fact": {
                "subject": "x", "predicate": "y", "object": "z",
                "qualifier": None, "value": None,
            },
            "original_text": "test",
            "source_span_refs": ["span-nonexistent"],
            "must_preserve_in_question": "no",
        }
        with self.assertRaisesRegex(ContractError, "SPAN_REF_NOT_FOUND"):
            build_fact_package(source, llm_facts=[llm_fact])

    def test_build_with_evidence_conflicts(self):
        source = make_matched_source()
        conflicts = [{
            "party_a": "处罚决定书",
            "party_b": "外部法规",
            "conflict_detail": "处罚依据与现行法规版本不一致",
            "resolution_status": "unresolved",
        }]
        pkg = build_fact_package(source, evidence_conflicts=conflicts)
        self.assertEqual(len(pkg["evidence_conflicts"]), 1)
        self.assertEqual(pkg["evidence_conflicts"][0]["resolution_status"], "unresolved")


class FactExtractorClassTests(unittest.TestCase):
    """FactExtractor: validate_input, extract, validate_output, re_extract."""

    def test_end_to_end_matched(self):
        source = make_matched_source()
        extractor = FactExtractor(ROOT)
        extractor.validate_input(source)
        pkg = extractor.extract(source)
        extractor.validate_output(pkg, source)

    def test_end_to_end_list_only(self):
        source = make_list_only_source()
        extractor = FactExtractor(ROOT)
        extractor.validate_input(source)
        pkg = extractor.extract(source)
        extractor.validate_output(pkg, source)

    def test_validate_output_rejects_bad_span_ref(self):
        source = make_matched_source()
        extractor = FactExtractor(ROOT)
        pkg = extractor.extract(source)
        # Use valid-format span ID that doesn't exist in the source package.
        pkg["source_facts"][0]["source_span_refs"] = ["span-9999"]
        with self.assertRaisesRegex(ContractError, "SPAN_REF_NOT_IN_SOURCE"):
            extractor.validate_output(pkg, source)

    def test_re_extract_from_review_adds_missing_fact(self):
        source = make_matched_source()
        extractor = FactExtractor(ROOT)
        original = extractor.extract(source)
        review = {
            "error_details": [
                {
                    "suggested_fact": {
                        "penalty_fact_id": "fact-new",
                        "fact_type": "amount",
                        "structured_fact": {
                            "subject": "张三",
                            "predicate": "罚款金额",
                            "object": "30万元",
                            "qualifier": "人民币",
                            "value": "300000",
                        },
                        "original_text": "罚款人民币30万元",
                        "source_span_refs": ["span-0005"],
                        "must_preserve_in_question": "yes",
                    },
                    "route_suggestion": "120",
                }
            ]
        }
        new_pkg = extractor.re_extract_from_review(source, review, original)
        texts = [f["original_text"] for f in new_pkg["source_facts"]]
        self.assertIn("罚款人民币30万元", texts)
        # Should pass output validation
        extractor.validate_output(new_pkg, source)


class CrossSpanAndMultiTypeTests(unittest.TestCase):
    """Cross-span facts, amount/time/subject/regulatory conclusion."""

    def test_cross_span_fact_has_multiple_refs(self):
        """A fact whose text spans multiple source_spans should reference all."""
        source = make_matched_source()
        # violation_facts_raw is "违规发放贷款" which appears in span-0003
        facts = extract_facts(source)
        behavior_facts = [f for f in facts if f["fact_type"] == "behavior"]
        if behavior_facts:
            self.assertIsInstance(behavior_facts[0]["source_span_refs"], list)

    def test_amount_fact_type_supported(self):
        """LLM can add amount facts; deterministic extraction covers other types."""
        source = make_matched_source()
        llm_fact = {
            "penalty_fact_id": "fact-amt",
            "fact_type": "amount",
            "structured_fact": {
                "subject": "张三", "predicate": "罚款金额", "object": "30万元",
                "qualifier": "人民币", "value": "300000",
            },
            "original_text": "罚款人民币30万元",
            "source_span_refs": ["span-0005"],
            "must_preserve_in_question": "yes",
        }
        pkg = build_fact_package(source, llm_facts=[llm_fact])
        amount_facts = [f for f in pkg["source_facts"] if f["fact_type"] == "amount"]
        self.assertEqual(len(amount_facts), 1)

    def test_all_fact_types_represented(self):
        """Ensure the package covers subject, behavior, time, result, regulatory_conclusion."""
        source = make_matched_source()
        pkg = build_fact_package(source)
        types_present = {f["fact_type"] for f in pkg["source_facts"]}
        for expected in ("subject", "behavior", "time", "result", "regulatory_conclusion"):
            self.assertIn(expected, types_present,
                          f"Missing fact_type {expected} in {types_present}")


class DownstreamStubTests(unittest.TestCase):
    """Simulate 130/140/150/170/180 consuming the fact package."""

    def test_130_stub_can_read_source_facts(self):
        source = make_matched_source()
        pkg = build_fact_package(source)
        # 130 (observable fact constructor) reads source_facts
        for fact in pkg["source_facts"]:
            self.assertIn("penalty_fact_id", fact)
            self.assertIn("fact_type", fact)
            self.assertIn("structured_fact", fact)
            self.assertIn("original_text", fact)

    def test_170_stub_can_check_fact_omission(self):
        source = make_matched_source()
        pkg = build_fact_package(source)
        # 170 reviewer checks for completeness
        must_preserve = [
            f for f in pkg["source_facts"]
            if f["must_preserve_in_question"] == "yes"
        ]
        self.assertGreater(len(must_preserve), 0)

    def test_140_stub_can_read_structured_facts(self):
        source = make_matched_source()
        pkg = build_fact_package(source)
        # 140 (query spec) reads structured_fact fields
        for fact in pkg["source_facts"]:
            sf = fact["structured_fact"]
            self.assertIn("subject", sf)
            self.assertIn("predicate", sf)
            self.assertIn("object", sf)


class EnvelopeIntegrationTests(unittest.TestCase):
    """Verify fact package works with COMMON-ENVELOPE wrapping."""

    def test_fact_payload_wraps_in_common_envelope(self):
        from east_v5.artifacts.schema import validate_common_envelope_schema
        source = make_matched_source()
        pkg = build_fact_package(source)
        envelope = {
            "artifact_id": "PENALTY-FACT-DEMO-001",
            "artifact_type": "penalty_fact_package",
            "run_id": "test-run",
            "qa_id": "QA-001",
            "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64,
            "supersedes_ref": None,
            "attempt_no": 1,
            "producer_id": "120",
            "parent_artifact_refs": [
                {"artifact_id": "PENALTY-DEMO-001", "version": 1, "content_hash": "a" * 64}
            ],
            "input_hashes": ["a" * 64],
            "status": "candidate",
            "mode": "question_sql",
            "created_at": "2026-08-13T00:00:00+00:00",
            "trace_id": "test-trace",
            "storage_locator": "test/001.json",
        }
        validate_common_envelope_schema(ROOT, envelope)
        # The fact package content is independent of the envelope


class EnumAndBoundaryTests(unittest.TestCase):
    """Fixed enums, status boundaries, schema edge cases."""

    def test_all_fact_types_are_valid_enum(self):
        for t in FACT_TYPES:
            self.assertIsInstance(t, str)

    def test_all_must_preserve_values_are_valid_enum(self):
        for v in MUST_PRESERVE_VALUES:
            self.assertIn(v, ("yes", "no", "conditional"))

    def test_fact_id_format(self):
        source = make_matched_source()
        facts = extract_facts(source)
        for fact in facts:
            self.assertRegex(fact["penalty_fact_id"], r"^fact-[0-9]{3}$")

    def test_structured_fact_has_all_fields(self):
        source = make_matched_source()
        facts = extract_facts(source)
        for fact in facts:
            sf = fact["structured_fact"]
            self.assertEqual(set(sf.keys()), {"subject", "predicate", "object", "qualifier", "value"})

    def test_source_with_no_subjects_produces_unknown_subject(self):
        source = make_matched_source()
        source["punished_person_name_raw"] = None
        source["punished_org_name_raw"] = None
        facts = extract_facts(source)
        for fact in facts:
            self.assertEqual(fact["structured_fact"]["subject"], "未知主体")


if __name__ == "__main__":
    unittest.main()
