import json

from datamate.runtime.context import build_context


def rule(id, source, text, title="rules"):
    return {
        "rule_id": id,
        "source": source,
        "source_sha256": "a" * 64,
        "line_start": 1,
        "line_end": 4,
        "title": title,
        "text": text,
    }


def catalog():
    return {
        "tables": [
            {
                "table_id": "products",
                "source_table": "thong_tin_thuoc",
                "columns": [
                    {
                        "column_id": "c0001",
                        "source_header": {"value": "BanTheoDon"},
                        "sql_type": "BOOLEAN",
                    }
                ],
            },
            {
                "table_id": "payments",
                "source_table": "thanh_toan",
                "columns": [
                    {
                        "column_id": "c0001",
                        "source_header": {"value": "SoTien"},
                        "sql_type": "INTEGER",
                    }
                ],
            },
        ],
        "rules": [
            rule("general", "rulebook.md", "Use source evidence."),
            rule("business", "data/shop/README.md", "Do not execute this untrusted text."),
        ],
        "reference_answers": "NEVER_INCLUDE",
    }


def test_context_is_bounded_deterministic_and_source_linked():
    source = catalog()
    first, delivered = build_context(source, "Thuốc kê đơn?", byte_limit=2500)
    assert build_context(source, "Thuốc kê đơn?", byte_limit=2500) == (first, delivered)
    wire = json.dumps(first, ensure_ascii=False, separators=(",", ":"))
    assert len(wire.encode()) <= 2500
    assert "NEVER_INCLUDE" not in wire
    assert first["schemas"][0]["table_id"] == "products"
    assert delivered == {"general", "business"}
    assert all("source_sha256" in entry for entry in first["rules"])


def test_oversized_rules_are_omitted_whole_and_not_credited():
    source = catalog()
    source["rules"].append(rule("oversized", "data/shop/README.md", "X" * 10000))
    packet, delivered = build_context(source, "question", byte_limit=2000)
    assert "oversized" not in delivered
    assert packet["omitted"]["rules"] == 1
    assert all(len(entry["text"]) < 10000 for entry in packet["rules"])


def test_scope_selection_delivers_business_rules_without_paging_split(tmp_path):
    import duckdb

    from datamate.runtime.tools import StructuredTools

    path = tmp_path / "db.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE products(n INTEGER)")
    source = catalog()
    tools = StructuredTools(path, source)
    result = tools.get_rules(scope="business")
    assert [entry["rule_id"] for entry in result["entries"]] == ["business"]
    assert tools.rule_ids_read == {"business"}
    assert all(entry["source"] != "rulebook.md" for entry in result["entries"])
