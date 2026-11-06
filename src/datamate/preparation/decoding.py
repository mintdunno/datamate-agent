"""Conservative, source-bound strict CSV decoder selection."""

import codecs
import csv
import io
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO, TextIO

from charset_normalizer import from_bytes

SAMPLE_BYTES = 65536
_ALLOWED = {"utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1258", "cp1252"}


class CsvDecodingError(ValueError):
    """A decoding failure with metadata only, never source text."""

    def __init__(self, message: str, candidates: tuple[dict[str, object], ...] = ()):
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class CsvDecodingDecision:
    encoding: str
    source_sha256: str
    reason: str
    candidates: tuple[dict[str, object], ...]


def csv_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


@contextmanager
def _snapshot(path: Path, expected: str | None = None) -> Iterator[tuple[BinaryIO, str]]:
    # A disk-backed private snapshot pins header and rows to one immutable byte stream.
    with TemporaryFile(mode="w+b") as snapshot:
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(SAMPLE_BYTES):
                digest.update(chunk)
                snapshot.write(chunk)
        actual = digest.hexdigest()
        if expected is not None and actual != expected:
            raise CsvDecodingError("Source SHA256 hash changed or does not match configuration")
        snapshot.seek(0)
        yield snapshot, actual


@contextmanager
def open_csv_source(path: Path, decision: CsvDecodingDecision) -> Iterator[TextIO]:
    """Read a private byte snapshot verified against the selected source hash."""
    with _snapshot(path, decision.source_sha256) as (snapshot, _):
        text = io.TextIOWrapper(snapshot, encoding=decision.encoding, errors="strict", newline="")
        try:
            yield text
        finally:
            text.detach()


def _validate(snapshot: BinaryIO, encoding: str) -> None:
    snapshot.seek(0)
    text = io.TextIOWrapper(snapshot, encoding=encoding, errors="strict", newline="")
    try:
        sample = text.read(SAMPLE_BYTES)
        if not sample:
            raise CsvDecodingError("Empty CSV file")
        text.seek(0)
        reader = csv.reader(text, delimiter=csv_delimiter(sample), strict=True)
        for row in reader:
            if any("\x00" in cell for cell in row):
                raise CsvDecodingError("NUL characters are not valid CSV text")
    finally:
        text.detach()


def _vietnamese_evidence(text: str) -> bool:
    # CP1258 represents Vietnamese tones as combining marks. Require independent
    # words and distinct tone marks; detector rank by itself is never evidence.
    tones = "\u0300\u0301\u0303\u0309\u0323"
    # Combining marks are not Python word characters: inspect space-delimited tokens.
    marked = [word for word in text.split() if any(tone in word for tone in tones)]
    marks = {char for char in text if char in tones}
    return len(marked) >= 3 and len(marks) >= 2 and any(c in text for c in "ăâêôơưđĂÂÊÔƠƯĐ")


def select_csv_decoder(
    path: Path, *, encoding: str | None = None, expected_sha256: str | None = None
) -> CsvDecodingDecision:
    """Select only a BOM, trusted hash-bound hint, UTF-8, or strong language evidence."""
    if encoding is not None and expected_sha256 is None:
        raise CsvDecodingError("An explicit encoding must be bound to a source SHA256 hash")
    if expected_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise CsvDecodingError("Invalid source SHA256 hash")
    if encoding is not None:
        try:
            encoding = codecs.lookup(encoding).name
        except LookupError:
            raise CsvDecodingError("Unsupported explicit CSV encoding") from None
        if encoding not in _ALLOWED:
            raise CsvDecodingError("Unsupported explicit CSV encoding")
    with _snapshot(path, expected_sha256) as (snapshot, digest):
        sample = snapshot.read(SAMPLE_BYTES)
        if not sample:
            raise CsvDecodingError("empty CSV file")
        if sample.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            raise CsvDecodingError("Unsupported UTF-32 BOM")
        bom = next(
            (
                codec
                for marker, codec in (
                    (codecs.BOM_UTF8, "utf-8-sig"),
                    (codecs.BOM_UTF16_LE, "utf-16"),
                    (codecs.BOM_UTF16_BE, "utf-16"),
                )
                if sample.startswith(marker)
            ),
            None,
        )
        if bom and encoding:
            compatible = (
                {"utf-8", "utf-8-sig"}
                if bom == "utf-8-sig"
                else {
                    "utf-16",
                    "utf-16-le" if sample.startswith(codecs.BOM_UTF16_LE) else "utf-16-be",
                }
            )
            if encoding not in compatible:
                raise CsvDecodingError("Explicit encoding conflicts with source BOM")
        selected = bom or encoding
        if selected:
            try:
                _validate(snapshot, selected)
            except (UnicodeError, csv.Error, CsvDecodingError):
                raise CsvDecodingError("Invalid CSV for trusted encoding or BOM") from None
            return CsvDecodingDecision(selected, digest, "bom" if bom else "source-bound hint", ())
        try:
            _validate(snapshot, "utf-8")
        except UnicodeError:
            pass
        except (csv.Error, CsvDecodingError):
            raise CsvDecodingError("Invalid CSV structure or NUL text") from None
        else:
            return CsvDecodingDecision("utf-8", digest, "strict UTF-8", ())
        ranked = [match.encoding for match in from_bytes(sample)]
        candidates: list[dict[str, object]] = []
        valid: dict[str, str] = {}
        for codec in sorted(
            ("cp1258", "cp1252", "utf-16-le", "utf-16-be"),
            key=lambda c: (
                ranked.index(c.replace("-", "_")) if c.replace("-", "_") in ranked else len(ranked)
            ),
        ):
            try:
                _validate(snapshot, codec)
                rendered = sample.decode(codec, errors="strict")
            except (UnicodeError, csv.Error, CsvDecodingError):
                candidates.append({"encoding": codec, "valid": False})
            else:
                valid[codec] = rendered
                candidates.append({"encoding": codec, "valid": True})
        receipt = tuple(candidates)
        if "cp1258" in valid and _vietnamese_evidence(valid["cp1258"]):
            return CsvDecodingDecision("cp1258", digest, "strong Vietnamese tone evidence", receipt)
        raise CsvDecodingError("Undecidable CSV encoding; provide a source-bound hint", receipt)
