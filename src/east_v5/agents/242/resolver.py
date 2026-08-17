"""Authoritative constraint-asset query resolver for Agent 242.

The resolver is constructed from a controlled :class:`ConstraintQueryService`
(production: a live, verified ``ConstraintAssetService`` wrapped by
:class:`ConstraintAssetQueryService`; see
:func:`build_constraint_asset_resolver`).  It queries CA-V0.2.0 (single-field),
CA-V0.3.0 (multi-field) and TRG-V1.0.0 (typed reference graph) *itself*, binds
every server-issued query receipt to the approved control plane, and enforces
strict completeness by walking the server's authoritative, single-use HMAC
cursor chain to closure — never by re-deriving ``total``/``complete`` in the
adapter.

The consumed receipt is the EAS-58 frozen server receipt format
(``v5.constraint-asset-query-result/v2``): ``query_method``, ``table_code``,
normalized ``query_parameters``, ``artifact_id + asset_version + content_hash``,
authoritative ``total``, page/cursor fields, ``returned_count``, ``complete``,
record-set hash (``records_hash``) and receipt hash (``receipt_hash``).  A
receipt whose source, method, table, page chain or hash drifts, or whose
pagination never closes, fails closed.

There is no arbitrary ``Callable`` that can mint a proof, and ``resolve``
returns the canonical rule content alongside the rule so any post-resolution
drift is re-checked against the enumerated authoritative hash.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from east_v5.artifacts import artifact_ref
from east_v5.constraint_assets.service import (
    CONTROL_PATH,
    ConstraintAssetService,
    _control_index,
    validate_query_receipt_contract,
)
from east_v5.governance import ContractError, sha256
from east_v5.validators import split_endpoint
from east_v5.validators.expression import make_rule

UNIVERSE_SCHEMA_VERSION = "v5.constraint-universe/v2"
QUERY_RECEIPT_SCHEMA_VERSION = "v5.constraint-asset-query-result/v2"

SOURCE_VERSIONS = frozenset({"CA-V0.2.0", "CA-V0.3.0", "TRG-V1.0.0"})
EXPECTED_SOURCE_BY_METHOD = {
    "multifield_constraints_for_table": ("CA-V0.3.0", "constraint_asset_ref", "constraints_for_table"),
    "field_rules_for_table": ("CA-V0.2.0", "constraint_asset_ref", "field_rules_for_table"),
    "graph_edges_for_table": ("TRG-V1.0.0", "typed_reference_graph_ref", "graph_edges_for_table"),
}

UNIVERSE_KEYS = {"schema_version", "closure_ref", "sources", "constraints", "content_sha256"}
SOURCE_KEYS = {"artifact_id", "content_hash"}
CONSTRAINT_KEYS = {"constraint_id", "scope", "source_asset_version", "canonical_rule_hash"}

# The exact record fields a server ``canonical_rule_hash`` covers, per source.
MULTIFIELD_CONTENT_KEYS = ("constraint_id", "constraint_item_type", "scope", "structured_expression_json", "evidence_refs_json")
FIELD_CONTENT_KEYS = ("field_id", "table_code", "field_code", "constraint_item_type", "value_json", "evidence_refs_json", "review_status")

LIMIT = 100


def _fail(code: str) -> None:
    raise ContractError(code)


def load_approved_sources(repo_root: Path, *, control_path: Path = CONTROL_PATH) -> dict[str, dict[str, str]]:
    """The approved constraint-asset identity from the frozen control plane.

    All three approved roles (CA-V0.2.0 / CA-V0.3.0 / TRG-V1.0.0) are read from
    the single control index; for the frozen ``CONTROL_PATH`` the CA-V0.2.0
    identity is identical to the reconciliation manifest's frozen registration.
    """
    control = _control_index(repo_root, control_path)
    return {
        version: {"artifact_id": control[version]["artifact_id"], "content_hash": control[version]["content_hash"]}
        for version in SOURCE_VERSIONS
    }


def universe_content_hash(universe: dict[str, Any]) -> str:
    return sha256({key: universe[key] for key in ("schema_version", "closure_ref", "sources", "constraints")})


def verify_universe(universe: dict[str, Any], structure_closure: dict[str, Any], approved: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Validate the resolver's enumeration proof against the resolver's approved
    sources and the exact structure closure, before trusting a single rule."""
    if not isinstance(universe, dict) or set(universe) != UNIVERSE_KEYS:
        _fail("UNIVERSE_PROOF_INVALID")
    if universe["schema_version"] != UNIVERSE_SCHEMA_VERSION:
        _fail("UNIVERSE_PROOF_INVALID")
    if universe["closure_ref"] != artifact_ref(structure_closure["envelope"]):
        _fail("UNIVERSE_CLOSURE_MISMATCH")

    sources = universe["sources"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_VERSIONS):
        _fail("UNIVERSE_SOURCE_SET_INVALID")
    for version in SOURCE_VERSIONS:
        item = sources[version]
        if not isinstance(item, dict) or set(item) != SOURCE_KEYS:
            _fail("UNIVERSE_SOURCE_REF_INVALID")
        if item != approved[version]:
            _fail("UNIVERSE_SOURCE_DRIFT")

    constraints = universe["constraints"]
    if not isinstance(constraints, list):
        _fail("UNIVERSE_PROOF_INVALID")
    seen: set[tuple[str, str]] = set()
    for ref in constraints:
        if not isinstance(ref, dict) or set(ref) != CONSTRAINT_KEYS:
            _fail("UNIVERSE_PROOF_INVALID")
        if not isinstance(ref["constraint_id"], str) or not ref["constraint_id"]:
            _fail("UNIVERSE_PROOF_INVALID")
        if ref["scope"] not in {"field", "within_table", "cross_table"}:
            _fail("UNIVERSE_PROOF_INVALID")
        if ref["source_asset_version"] not in SOURCE_VERSIONS:
            _fail("UNIVERSE_SOURCE_VERSION_INVALID")
        if not isinstance(ref["canonical_rule_hash"], str) or len(ref["canonical_rule_hash"]) != 64:
            _fail("UNIVERSE_RULE_HASH_INVALID")
        key = (ref["constraint_id"], ref["scope"])
        if key in seen:
            _fail("UNIVERSE_DUPLICATE_CONSTRAINT")
        seen.add(key)

    if universe["content_sha256"] != universe_content_hash(universe):
        _fail("UNIVERSE_PROOF_INVALID")
    return universe


# ---------------------------------------------------------------------------
# Server receipt chain
# ---------------------------------------------------------------------------

def verify_query_receipts(
    pages: list[dict[str, Any]],
    approved: dict[str, dict[str, str]],
    *,
    method: str,
    table_code: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify a complete, contiguous chain of EAS-58 server receipts.

    Every page must be a genuine ``v5.constraint-asset-query-result/v2`` receipt
    for the expected method/table, bound to the approved source identity, and
    the cursor chain must walk from ``None`` to a final ``complete`` page whose
    records sum exactly to the server's authoritative ``total``.  Returns the
    concatenated records and the shared source identity.
    """
    expected_version, expected_type, service_method = EXPECTED_SOURCE_BY_METHOD[method]
    if not isinstance(pages, list) or not pages:
        _fail("QUERY_RECEIPT_INVALID")
    records: list[dict[str, Any]] = []
    total: int | None = None
    source: dict[str, Any] | None = None
    expected_cursor: str | None = None
    for page in pages:
        validate_query_receipt_contract(page, query_method=service_method, table_code=table_code)
        if page["asset_version"] != expected_version or page["artifact_type"] != expected_type:
            _fail("QUERY_RECEIPT_SOURCE_MISMATCH")
        expected = approved[page["asset_version"]]
        if page["artifact_id"] != expected["artifact_id"] or page["content_hash"] != expected["content_hash"]:
            _fail("UNIVERSE_SOURCE_DRIFT")
        if page["cursor"] != expected_cursor:
            _fail("QUERY_RECEIPT_INCOMPLETE")
        page_source = {key: page[key] for key in ("artifact_type", "artifact_id", "asset_version", "content_hash")}
        if total is None:
            total, source = page["total"], page_source
        elif page["total"] != total or page_source != source:
            _fail("QUERY_RECEIPT_INCOMPLETE")
        records.extend(page["records"])
        expected_cursor = page["next_cursor"]
    if total is None or len(records) != total:
        _fail("QUERY_RECEIPT_INCOMPLETE")
    last = pages[-1]
    if last["complete"] is not True or last["next_cursor"] is not None:
        _fail("QUERY_RECEIPT_INCOMPLETE")
    return records, source  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Controlled query service + resolver
# ---------------------------------------------------------------------------

class ConstraintQueryService(Protocol):
    """Controlled, manifest-verified constraint asset queries.

    Each method returns an ordered list of genuine EAS-58 server receipts
    (``v5.constraint-asset-query-result/v2``) that walk the server's
    single-use cursor chain from the first page to a final ``complete`` page.
    """

    def multifield_constraints_for_table(self, table_code: str, *, limit: int = LIMIT) -> list[dict[str, Any]]: ...

    def field_rules_for_table(self, table_code: str, *, limit: int = LIMIT) -> list[dict[str, Any]]: ...

    def graph_edges_for_table(self, table_code: str, *, limit: int = LIMIT) -> list[dict[str, Any]]: ...


def _multifield_content(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in MULTIFIELD_CONTENT_KEYS}


def _field_content(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in FIELD_CONTENT_KEYS}


def _public_receipt(page: dict[str, Any]) -> dict[str, Any]:
    """The public receipt stored in the output contract: the server's private
    ``service_proof`` (a per-instance MAC, not reproducible across runs) is
    dropped so the verified package identity stays deterministic."""
    return {key: value for key, value in page.items() if key != "service_proof"}


class AssetBoundResolver:
    """Enumerates the applicable universe by querying the controlled service and
    binding every rule to its server-issued authoritative ``canonical_rule_hash``."""

    def __init__(self, repo_root: Path, service: ConstraintQueryService, *, control_path: Path = CONTROL_PATH):
        self.repo_root = repo_root.resolve()
        self.control_path = control_path
        self.sources = load_approved_sources(self.repo_root, control_path=control_path)
        self.service = service
        self._rules: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
        self.receipts: list[dict[str, Any]] = []

    def enumerate(self, structure_closure: dict[str, Any]) -> dict[str, Any]:
        closure_ref = artifact_ref(structure_closure["envelope"])
        tables = structure_closure["payload"]["tables"]
        self._rules = {}
        self.receipts = []
        constraints: list[dict[str, Any]] = []
        graph_edges: set[tuple[str, str]] = set()

        for table_code in tables:
            m_pages = self.service.multifield_constraints_for_table(table_code)
            m_records, m_source = verify_query_receipts(m_pages, self.sources, method="multifield_constraints_for_table", table_code=table_code)
            f_pages = self.service.field_rules_for_table(table_code)
            f_records, f_source = verify_query_receipts(f_pages, self.sources, method="field_rules_for_table", table_code=table_code)
            g_pages = self.service.graph_edges_for_table(table_code)
            g_records, g_source = verify_query_receipts(g_pages, self.sources, method="graph_edges_for_table", table_code=table_code)
            self.receipts.extend(_public_receipt(page) for page in (*m_pages, *f_pages, *g_pages))
            self._ingest_multifield(m_records, m_source, constraints)
            self._ingest_field(f_records, f_source, constraints)
            self._ingest_graph(g_records, graph_edges)

        self._verify_references_covered(structure_closure["payload"], graph_edges)

        universe: dict[str, Any] = {
            "schema_version": UNIVERSE_SCHEMA_VERSION,
            "closure_ref": closure_ref,
            "sources": self.sources,
            "constraints": constraints,
        }
        universe["content_sha256"] = universe_content_hash(universe)
        return universe

    def _add(self, key: tuple[str, str], rule: dict[str, Any], content: dict[str, Any], scope: str, source_asset_version: str, constraints: list[dict[str, Any]]) -> None:
        existing = self._rules.get(key)
        if existing is not None:
            # A cross-table rule is legitimately returned by the service for
            # every table it spans; an identical re-emission is idempotent, a
            # different body under the same identity is a forgery.
            if existing[1] == content:
                return
            _fail("UNIVERSE_DUPLICATE_CONSTRAINT")
        self._rules[key] = (rule, content)
        constraints.append({
            "constraint_id": key[0],
            "scope": scope,
            "source_asset_version": source_asset_version,
            "canonical_rule_hash": sha256(content),
        })

    def _ingest_multifield(self, records: list[dict[str, Any]], source: dict[str, Any], constraints: list[dict[str, Any]]) -> None:
        for record in records:
            content = _multifield_content(record)
            if record["canonical_rule_hash"] != sha256(content):
                _fail("RULE_CONTENT_HASH_DRIFT")
            if record["scope"] == "INTRA_TABLE":
                scope = "within_table"
            elif record["scope"] == "CROSS_TABLE":
                scope = "cross_table"
            else:
                _fail("QUERY_RECEIPT_INVALID")
            expression = json.loads(record["structured_expression_json"])
            rule = make_rule(record["constraint_id"], source["artifact_id"], source["asset_version"], record["constraint_item_type"], record["scope"], expression)
            self._add((record["constraint_id"], scope), rule, content, scope, source["asset_version"], constraints)

    def _ingest_field(self, records: list[dict[str, Any]], source: dict[str, Any], constraints: list[dict[str, Any]]) -> None:
        for record in records:
            content = _field_content(record)
            if record["canonical_rule_hash"] != sha256(content):
                _fail("RULE_CONTENT_HASH_DRIFT")
            constraint_id = f"{record['field_id']}:{record['constraint_item_type']}"
            rule = {
                "rule_kind": record["constraint_item_type"],
                "constraint_id": constraint_id,
                "asset_id": source["artifact_id"],
                "asset_version": source["asset_version"],
                "endpoint": f"{record['table_code']}.{record['field_code']}",
                "spec": json.loads(record["value_json"]),
            }
            self._add((constraint_id, "field"), rule, content, "field", source["asset_version"], constraints)

    @staticmethod
    def _ingest_graph(records: list[dict[str, Any]], graph_edges: set[tuple[str, str]]) -> None:
        for edge in records:
            if not isinstance(edge, dict) or not isinstance(edge.get("provider_table_code"), str) or not edge["provider_table_code"] or not isinstance(edge.get("consumer_table_code"), str) or not edge["consumer_table_code"]:
                _fail("QUERY_RECEIPT_INVALID")
            if edge.get("canonical_edge_hash") is not None and edge["canonical_edge_hash"] != sha256({key: value for key, value in edge.items() if key != "canonical_edge_hash"}):
                _fail("QUERY_RECEIPT_INVALID")
            graph_edges.add((edge["provider_table_code"], edge["consumer_table_code"]))

    @staticmethod
    def _verify_references_covered(closure: dict[str, Any], graph_edges: set[tuple[str, str]]) -> None:
        for reference in closure.get("references", []):
            data = reference.get("data") if isinstance(reference, dict) else None
            if not isinstance(data, dict):
                continue
            source, target = data.get("from"), data.get("to")
            if isinstance(source, str) and isinstance(target, str):
                provider_table = split_endpoint(target)[0]
                consumer_table = split_endpoint(source)[0]
                if (provider_table, consumer_table) not in graph_edges:
                    _fail("GRAPH_REFERENCE_UNCOVERED")

    def resolve(self, constraint_id: str, scope: str) -> tuple[dict[str, Any], dict[str, Any]]:
        entry = self._rules.get((constraint_id, scope))
        if entry is None:
            _fail("UNKNOWN_CONSTRAINT")
        return entry


# ---------------------------------------------------------------------------
# Production construction
# ---------------------------------------------------------------------------

class ConstraintAssetQueryService:
    """Adapts the verified ``ConstraintAssetService`` to the controlled query
    interface by walking its server-issued, single-use cursor chain to closure.

    ``total``/``complete``/cursor/receipt are all read from the server and never
    recomputed here; a non-``ConstraintAssetService`` cannot stand in.
    """

    def __init__(self, service: Any):
        if type(service) is not ConstraintAssetService:
            _fail("ASSET_QUERY_SERVICE_REQUIRED")
        self.service = service

    def _pages(self, method_name: str, table_code: str, limit: int) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = getattr(self.service, method_name)(table_code, limit=limit, cursor=cursor)
            pages.append(page)
            if page["complete"]:
                return pages
            next_cursor = page["next_cursor"]
            if not next_cursor or next_cursor == cursor:
                _fail("ASSET_QUERY_CHAIN_GAP")
            cursor = next_cursor

    def multifield_constraints_for_table(self, table_code: str, *, limit: int = LIMIT) -> list[dict[str, Any]]:
        return self._pages("constraints_for_table", table_code, limit)

    def field_rules_for_table(self, table_code: str, *, limit: int = LIMIT) -> list[dict[str, Any]]:
        return self._pages("field_rules_for_table", table_code, limit)

    def graph_edges_for_table(self, table_code: str, *, limit: int = LIMIT) -> list[dict[str, Any]]:
        return self._pages("graph_edges_for_table", table_code, limit)


def build_constraint_asset_resolver(repo_root: Path, roots: dict[str, Any], manifest_path: Path, *, control_path: Path = CONTROL_PATH) -> AssetBoundResolver:
    """Production construction: bind the verified runtime manifest to
    ``ConstraintAssetService`` and wrap it in the resolver."""
    service = ConstraintAssetService(repo_root, roots, manifest_path, control_path=control_path)
    return AssetBoundResolver(repo_root, ConstraintAssetQueryService(service), control_path=control_path)
