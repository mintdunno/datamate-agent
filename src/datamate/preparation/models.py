"""Streaming source contracts shared by loaders and preparation."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

RawValue = str | int | float | bool | bytes | date | datetime | time | None


@dataclass(frozen=True)
class SourceRow:
    number: int
    values: tuple[RawValue, ...]
    formulas: tuple[str | None, ...] = ()
    formats: tuple[str | None, ...] = ()
    record_key: dict[str, RawValue] | None = None


@dataclass(frozen=True)
class SourceTable:
    """Rows must be consumed before advancing the containing table iterator."""

    name: str
    headers: tuple[RawValue, ...]
    header_row: int | None
    rows: Iterable[SourceRow]
    preamble: tuple[SourceRow, ...] = ()
    declared_types: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class BusinessInputs:
    split_id: str
    business_id: str
    split_root: Path
    business_root: Path
    sources: tuple[SourceFile, ...]
    documents: tuple[SourceFile, ...]
