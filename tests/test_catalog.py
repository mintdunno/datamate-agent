"""Rule retrieval preserves evidence text and source coordinates."""

import pytest

from datamate.preparation.catalog import rule_entries, search_rules
from datamate.preparation.discovery import sha256_file
from datamate.preparation.models import SourceFile


def test_catalog_preserves_rule_sections_scope_and_lexical_lookup(tmp_path):
    path = tmp_path / "rulebook.md"
    path.write_text(
        "# Doanh thu\nLoại đơn trả hàng.\n## Thời gian\nDùng as_of_date.\n", encoding="utf-8"
    )
    entries = rule_entries((SourceFile(path, "rulebook.md", sha256_file(path)),))
    assert len(entries) == 2
    assert entries[0]["line_start"] == 1 and entries[0]["line_end"] == 2
    assert entries[0]["scope"] == "split" and entries[0]["untrusted"] is True
    assert entries[0]["source_sha256"] == sha256_file(path)
    assert search_rules(entries, "doanh thu", limit=1)[0]["title"] == "Doanh thu"
    assert search_rules(entries, "absent") == []
    with pytest.raises(ValueError):
        search_rules(entries, "doanh thu", limit=0)
