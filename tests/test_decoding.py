"""Synthetic regression coverage for strict source-bound CSV decoding."""

from hashlib import sha256

import pytest

from datamate.preparation.decoding import CsvDecodingError, select_csv_decoder
from datamate.preparation.loaders import load_tables


def write_source(tmp_path, payload):
    path = tmp_path / "source.csv"
    path.write_bytes(payload)
    return path


@pytest.mark.parametrize("codec", ["utf-8", "utf-8-sig", "utf-16", "utf-16-be"])
def test_unicode_decoding_preserves_values(tmp_path, codec):
    text = "name,note\nĐặng,Việt Nam\n"
    payload = text.encode(codec)
    if codec == "utf-16-be":
        payload = b"\xfe\xff" + payload
    path = write_source(tmp_path, payload)
    table = next(load_tables(path))
    assert table.headers == ("name", "note")
    assert list(table.rows)[0].values == ("Đặng", "Việt Nam")
    assert table.decoding["source_sha256"] == sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "codec,text", [("cp1252", "name\ncafé\n"), ("cp1258", "name\nViê\u0323t\n")]
)
def test_explicit_legacy_encoding_is_source_bound(tmp_path, codec, text):
    payload = text.encode(codec)
    path = write_source(tmp_path, payload)
    table = next(load_tables(path, encoding=codec, expected_sha256=sha256(payload).hexdigest()))
    assert list(table.rows)[0].values == (text.splitlines()[1],)
    with pytest.raises(CsvDecodingError, match="hash|SHA"):
        next(load_tables(path, encoding=codec, expected_sha256="0" * 64))
    with pytest.raises(CsvDecodingError, match="hash|SHA|bound"):
        next(load_tables(path, encoding=codec))


def test_ambiguous_legacy_requires_hint(tmp_path):
    path = write_source(tmp_path, b"name\ncaf\xe9\n")
    with pytest.raises(CsvDecodingError, match="[Aa]mbiguous|[Uu]ndecidable") as error:
        select_csv_decoder(path)
    assert error.value.candidates
    assert "café" not in str(error.value)


def test_clear_vietnamese_cp1258_evidence(tmp_path):
    text = "name,note\nTiê\u0301ng Viê\u0323t,ngươ\u0300i Viê\u0323t\n"
    path = write_source(tmp_path, text.encode("cp1258"))
    decision = select_csv_decoder(path)
    assert decision.encoding == "cp1258"
    assert list(next(load_tables(path)).rows)[0].values == tuple(text.splitlines()[1].split(","))


@pytest.mark.parametrize(
    "payload", [b"\xff\xfe\x00\x00a\x00\x00\x00", b"a,b\n1,\x00\n", b'a,b\n"unterminated']
)
def test_invalid_unicode_or_csv_is_rejected(tmp_path, payload):
    with pytest.raises(CsvDecodingError):
        select_csv_decoder(write_source(tmp_path, payload))


def test_late_invalid_bytes_are_not_hidden_by_utf8_sample(tmp_path):
    path = write_source(tmp_path, b"a,b\n" + b"1,2\n" * 20000 + b"3,\xff\n")
    with pytest.raises(CsvDecodingError):
        next(load_tables(path))


def test_bom_conflict_is_rejected(tmp_path):
    payload = "name\nvalue\n".encode("utf-16")
    path = write_source(tmp_path, payload)
    with pytest.raises(CsvDecodingError, match="BOM|conflict"):
        select_csv_decoder(path, encoding="cp1252", expected_sha256=sha256(payload).hexdigest())


def test_source_drift_between_header_and_rows_is_rejected(tmp_path):
    path = write_source(tmp_path, b"name\nfirst\n")
    table = next(load_tables(path))
    path.write_bytes(b"name\nother\n")
    with pytest.raises(CsvDecodingError, match="changed|hash|SHA"):
        list(table.rows)


def test_ragged_csv_remains_a_row_error(tmp_path):
    table = next(load_tables(write_source(tmp_path, b"a,b\n1,2,3\n")))
    with pytest.raises(ValueError, match="Ragged"):
        list(table.rows)
