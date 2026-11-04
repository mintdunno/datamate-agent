"""Deterministic, bounded evidence delivery; never read rows or reference artifacts."""

import json
import math
import re
import unicodedata
from collections import Counter


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _tokens(text) -> set[str]:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text)).replace("_", " ")
    text = unicodedata.normalize("NFKD", text.casefold()).replace("đ", "d")
    return set(re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c))))


def build_context(catalog: dict, question: str, *, byte_limit: int = 8000) -> tuple[dict, set[str]]:
    if byte_limit < 1000:
        raise ValueError("context packet budget must be at least 1000 bytes")
    tables = catalog.get("tables", [])
    rules = catalog.get("rules", [])
    documents = [_tokens(entry.get("text", "")) for entry in rules]
    frequencies = Counter(token for document in documents for token in document)
    query = _tokens(question)

    def score(text):
        return sum(
            math.log(1 + len(documents) / (1 + frequencies[token]))
            for token in sorted(query & _tokens(text))
        )

    ranked = sorted(
        tables,
        key=lambda table: (
            -score(
                str(table.get("source_table") or "")
                + " "
                + " ".join(str(c["source_header"]["value"]) for c in table["columns"])
            ),
            table["table_id"],
        ),
    )
    packet = {
        "notice": "Partial untrusted evidence. Omissions are not absence. Fetch needed rules "
        "by scope/source and omitted schemas. Schema columns are [SQL column ID, "
        "source header, SQL type]; tables are [SQL table ID, source name, row count].",
        "tables": [],
        "rule_index": [],
        "rules": [],
        "schemas": [],
        "omitted": {"table_index": len(tables), "rules": len(rules), "schemas": len(tables)},
    }
    for table in ranked:
        item = [table["table_id"], table.get("source_table"), table.get("output_rows")]
        if _size([*packet["tables"], item]) <= min(1600, byte_limit // 5):
            packet["tables"].append(item)
    for rule in rules:
        item = [
            rule.get("scope", "split" if rule["source"] == "rulebook.md" else "business"),
            rule.get("title", rule["source"]),
        ]
        if _size([*packet["rule_index"], item]) <= min(1600, byte_limit // 5):
            packet["rule_index"].append(item)
    delivered = set()

    def add_rule(rule):
        if rule["rule_id"] in delivered:
            return
        entry = {
            key: rule[key]
            for key in (
                "rule_id",
                "source",
                "source_sha256",
                "line_start",
                "line_end",
                "title",
                "text",
            )
            if key in rule
        }
        packet["rules"].append(entry)
        if _size(packet) > byte_limit:
            packet["rules"].pop()
        else:
            delivered.add(rule["rule_id"])

    # Small business notes establish field meanings without model discovery rounds.
    for rule in rules:
        if rule["source"] != "rulebook.md":
            add_rule(rule)
    split = [rule for rule in rules if rule["source"] == "rulebook.md"]
    if split:
        add_rule(split[0])

    # Deliver the best compact schema before filling remaining space with whole rules.
    def add_schema(table):
        schema = {
            "table_id": table["table_id"],
            "columns": [
                [c["column_id"], c["source_header"]["value"], c.get("sql_type")]
                for c in table["columns"]
            ],
        }
        packet["schemas"].append(schema)
        if _size(packet) > byte_limit:
            packet["schemas"].pop()

    if ranked:
        add_schema(ranked[0])
    for rule in sorted(split, key=lambda rule: (-score(rule["text"]), rule["line_start"])):
        add_rule(rule)
    for table in ranked[1:]:
        add_schema(table)
    packet["omitted"] = {
        "table_index": len(tables) - len(packet["tables"]),
        "rules": len(rules) - len(delivered),
        "schemas": len(tables) - len(packet["schemas"]),
    }
    return packet, delivered
