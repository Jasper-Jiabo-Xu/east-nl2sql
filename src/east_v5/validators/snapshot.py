"""Read-only candidate-data snapshot used by every validator.

The 242 validator never writes candidate data or the formal store; it only
reads rows through this frozen view.  ``Snapshot`` and ``Table`` expose no
mutating methods, so a validator cannot accidentally alter its input.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from east_v5.governance import ContractError

from east_v5.validators.result import ERROR_INVALID_INPUT

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ENDPOINT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


def _fail(code: str) -> None:
    raise ContractError(code)


def split_endpoint(endpoint: str) -> tuple[str, str]:
    """Split a ``TABLE.FIELD`` endpoint into its two identifier parts."""
    if not isinstance(endpoint, str) or not _ENDPOINT.fullmatch(endpoint):
        _fail(ERROR_INVALID_INPUT)
    table_code, field_code = endpoint.split(".", 1)
    return table_code, field_code


def is_empty(value: Any) -> bool:
    """An EAST field is treated as absent when null or the empty string."""
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    return False


class Table:
    """A frozen view over one table's rows: ``[{field_code: value}, ...]``."""

    def __init__(self, table_code: str, rows: list[dict[str, Any]]):
        if not isinstance(table_code, str) or not _IDENTIFIER.fullmatch(table_code):
            _fail(ERROR_INVALID_INPUT)
        if not isinstance(rows, list):
            _fail(ERROR_INVALID_INPUT)
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not all(isinstance(key, str) and _IDENTIFIER.fullmatch(key) for key in row):
                _fail(ERROR_INVALID_INPUT)
        self.table_code = table_code
        self.rows = list(rows)

    def __len__(self) -> int:
        return len(self.rows)

    def value(self, field_code: str, record_index: int) -> Any:
        if not isinstance(field_code, str) or not _IDENTIFIER.fullmatch(field_code):
            _fail(ERROR_INVALID_INPUT)
        if not isinstance(record_index, int) or isinstance(record_index, bool) or not 0 <= record_index < len(self.rows):
            _fail(ERROR_INVALID_INPUT)
        return self.rows[record_index].get(field_code)

    def column(self, field_code: str) -> list[Any]:
        """All raw values (including nulls) of one column, in row order."""
        if not isinstance(field_code, str) or not _IDENTIFIER.fullmatch(field_code):
            _fail(ERROR_INVALID_INPUT)
        return [row.get(field_code) for row in self.rows]

    def present_values(self, field_code: str) -> set[Any]:
        """The set of non-empty values of one column (for membership lookups)."""
        return {value for value in self.column(field_code) if not is_empty(value)}


class Snapshot:
    """A frozen multi-table view keyed by table code."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        if not isinstance(tables, dict):
            _fail(ERROR_INVALID_INPUT)
        self.tables: dict[str, Table] = {}
        for table_code, rows in tables.items():
            table = Table(table_code, rows)
            self.tables[table_code] = table

    def table(self, table_code: str) -> Table:
        if not isinstance(table_code, str) or table_code not in self.tables:
            _fail(ERROR_INVALID_INPUT)
        return self.tables[table_code]

    def endpoint_value(self, endpoint: str, record_index: int) -> Any:
        table_code, field_code = split_endpoint(endpoint)
        return self.table(table_code).value(field_code, record_index)

    def endpoint_present_values(self, endpoint: str) -> set[Any]:
        table_code, field_code = split_endpoint(endpoint)
        return self.table(table_code).present_values(field_code)
