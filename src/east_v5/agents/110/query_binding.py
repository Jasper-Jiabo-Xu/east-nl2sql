"""Deterministic v1 named-query parameter parsing and source authentication.

This is deliberately a small SQL lexer, not a SQL rewriter.  It finds only
SQLite ``:name`` placeholders outside literals, quoted identifiers and
comments, and never substitutes values into SQL text.
"""
from __future__ import annotations

import re
from typing import Any

from east_v5.governance import ContractError


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_POINTER = re.compile(r"^/(query_entry/entry_conditions|filters_and_evidence)/(0|[1-9][0-9]*)/value$")
_NULL_OPERATORS = {"IS", "IS NOT"}


def _fail(code: str) -> None:
    raise ContractError(code)


def named_placeholders(sql: Any) -> tuple[str, ...]:
    """Return sorted unique v1 placeholders, rejecting every other form."""
    if not isinstance(sql, str):
        _fail("QUERY_PARAMETER_SQL_INVALID")
    found: set[str] = set()
    index, length = 0, len(sql)
    state = "normal"
    while index < length:
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < length else ""
        if state == "single":
            if char == "'":
                if next_char == "'": index += 2; continue
                state = "normal"
            index += 1; continue
        if state == "double":
            if char == '"':
                if next_char == '"': index += 2; continue
                state = "normal"
            index += 1; continue
        if state == "backtick":
            if char == "`": state = "normal"
            index += 1; continue
        if state == "bracket":
            if char == "]": state = "normal"
            index += 1; continue
        if state == "line":
            if char in "\r\n": state = "normal"
            index += 1; continue
        if state == "block":
            if char == "*" and next_char == "/": state = "normal"; index += 2; continue
            index += 1; continue
        if char == "'": state = "single"; index += 1; continue
        if char == '"': state = "double"; index += 1; continue
        if char == "`": state = "backtick"; index += 1; continue
        if char == "[": state = "bracket"; index += 1; continue
        if char == "-" and next_char == "-": state = "line"; index += 2; continue
        if char == "/" and next_char == "*": state = "block"; index += 2; continue
        if char == "?": _fail("QUERY_PARAMETER_STYLE_REJECTED")
        if char in "@$" and _NAME.match(sql, index + 1): _fail("QUERY_PARAMETER_STYLE_REJECTED")
        if char == ":":
            match = _NAME.match(sql, index + 1)
            if match is None or (index and sql[index - 1] == ":"):
                _fail("QUERY_PARAMETER_STYLE_REJECTED")
            found.add(match.group(0)); index = match.end(); continue
        index += 1
    if state in {"single", "double", "backtick", "bracket", "block"}:
        _fail("QUERY_PARAMETER_SQL_INVALID")
    return tuple(sorted(found))


def validate_declarations(sql: str, declarations: Any) -> tuple[str, ...]:
    placeholders = named_placeholders(sql)
    if not isinstance(declarations, list): _fail("QUERY_PARAMETER_DECLARATIONS_INVALID")
    names: list[str] = []
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {"name", "source_pointer"}:
            _fail("QUERY_PARAMETER_DECLARATIONS_INVALID")
        name, pointer = declaration.get("name"), declaration.get("source_pointer")
        if not isinstance(name, str) or _NAME.fullmatch(name) is None or not isinstance(pointer, str) or _POINTER.fullmatch(pointer) is None:
            _fail("QUERY_PARAMETER_DECLARATIONS_INVALID")
        names.append(name)
    if len(names) != len(set(names)): _fail("QUERY_PARAMETER_DECLARATION_DUPLICATE")
    if set(names) != set(placeholders): _fail("QUERY_PARAMETER_SET_MISMATCH")
    return placeholders


def _scalar_type(value: Any) -> str:
    if value is None: return "null"
    if isinstance(value, str): return "text"
    if isinstance(value, int) and not isinstance(value, bool): return "integer"
    if isinstance(value, float): return "real"
    _fail("QUERY_PARAMETER_TYPE_REJECTED")


def resolve_declaration(declaration: dict[str, Any], query_spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve exactly one allowed pointer; no database, fixture or ORM input."""
    pointer = declaration["source_pointer"]
    match = _POINTER.fullmatch(pointer)
    if match is None: _fail("QUERY_SPEC_ERROR:PARAMETER_POINTER_INVALID")
    source, offset = match.group(1), int(match.group(2))
    payload = query_spec["payload"]
    if source == "query_entry/entry_conditions":
        items = payload["query_entry"]["entry_conditions"]
        if offset >= len(items): _fail("QUERY_SPEC_ERROR:PARAMETER_POINTER_MISSING")
        item = items[offset]; table = payload["query_entry"]["entry_table"]; evidence = None
    else:
        items = payload["filters_and_evidence"]
        if offset >= len(items): _fail("QUERY_SPEC_ERROR:PARAMETER_POINTER_MISSING")
        item = items[offset]
        candidates = [entry["table_id"] for entry in payload["sql_schema_scope"]["allowed_tables"] if item["field_id"] in entry["allowed_fields"]]
        if len(candidates) != 1: _fail("QUERY_SPEC_ERROR:PARAMETER_FIELD_AMBIGUOUS")
        table, evidence = candidates[0], item["evidence_ref"]
    value, operator = item["value"], item["operator"]
    value_type = _scalar_type(value)
    if (value_type == "null") != (operator.upper() in _NULL_OPERATORS):
        _fail("QUERY_SPEC_ERROR:PARAMETER_NULL_OPERATOR_MISMATCH")
    return {"name": declaration["name"], "source_pointer": pointer, "field": f"{table}.{item['field_id']}", "operator": operator, "value": value, "sqlite_type": value_type, "evidence_ref": evidence}


def query_bindings(binding: dict[str, Any]) -> dict[str, Any]:
    """Return the Python sqlite parameter map after strict payload checks."""
    parameters = binding["payload"].get("parameters")
    if not isinstance(parameters, list): _fail("QUERY_PARAMETER_BINDING_INVALID")
    result: dict[str, Any] = {}
    for item in parameters:
        if not isinstance(item, dict) or item.get("sqlite_type") != _scalar_type(item.get("value")):
            _fail("QUERY_PARAMETER_TYPE_DRIFT")
        if item["name"] in result: _fail("QUERY_PARAMETER_DECLARATION_DUPLICATE")
        result[item["name"]] = item["value"]
    return result
