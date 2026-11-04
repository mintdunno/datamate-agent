"""Bounded-memory profiles cover every record without retaining value samples."""

import hashlib
import json
from collections import Counter
from decimal import Decimal

import polars as pl

from datamate.preparation.normalization import PreparedRow, encoded


class TableProfiler:
    def __init__(self, width: int):
        self.columns = [
            {
                "column_id": f"c{i + 1:04d}",
                "raw_types": Counter(),
                "normalized_types": Counter(),
                "null_count": 0,
                "blank_count": 0,
                "normalized_null_count": 0,
                "parse_failures": 0,
                "unique": set(),
                "unique_count_exact": True,
                "integer_digits": 0,
                "scale": 0,
                "timezone_modes": set(),
            }
            for i in range(width)
        ]
        self.input_rows = 0
        self.excluded_rows = 0

    def consume(self, rows: list[PreparedRow]) -> None:
        self.input_rows += len(rows)
        self.excluded_rows += sum(row.excluded for row in rows)
        for index, profile in enumerate(self.columns):
            kinds = pl.Series("kind", [type(row.source.values[index]).__name__ for row in rows])
            profile["raw_types"].update(dict(kinds.value_counts().iter_rows()))
            for row in rows:
                raw, value = row.source.values[index], row.values[index]
                profile["normalized_types"][type(value).__name__] += 1
                if type(value).__name__ == "datetime":
                    profile["timezone_modes"].add(
                        "aware" if value.utcoffset() is not None else "naive"
                    )
                profile["null_count"] += raw is None
                profile["blank_count"] += type(raw) is str and raw == ""
                profile["normalized_null_count"] += value is None
                profile["parse_failures"] += any(
                    e["column"] == profile["column_id"] and e["code"] == "parse_failed"
                    for e in row.events
                )
                key = hashlib.sha256(json.dumps(encoded(raw), sort_keys=True).encode()).digest()
                if len(profile["unique"]) < 1000:
                    profile["unique"].add(key)
                elif key not in profile["unique"]:
                    profile["unique_count_exact"] = False
                if type(value) is int or isinstance(value, Decimal):
                    number = Decimal(value) if type(value) is int else value
                    parts = number.as_tuple()
                    profile["integer_digits"] = max(
                        profile["integer_digits"], len(parts.digits) + parts.exponent, 0
                    )
                    profile["scale"] = max(profile["scale"], -parts.exponent, 0)

    def finish(self) -> list[dict]:
        results = []
        for profile in self.columns:
            types = set(profile["normalized_types"]) - {"NoneType"}
            sql_type = "VARCHAR"
            if types and types <= {"int", "Decimal"}:
                scale = profile["scale"]
                if scale + profile["integer_digits"] > 38:
                    raise ValueError(
                        "numeric precision exceeds DuckDB DECIMAL(38); choose explicit text policy"
                    )
                sql_type = f"DECIMAL(38,{scale})"
            elif types == {"bool"}:
                sql_type = "BOOLEAN"
            elif types == {"date"}:
                sql_type = "DATE"
            elif types == {"datetime"}:
                if profile["timezone_modes"] == {"aware"}:
                    sql_type = "TIMESTAMPTZ"
                elif profile["timezone_modes"] == {"naive"}:
                    sql_type = "TIMESTAMP"
            elif types == {"time"}:
                sql_type = "TIME"
            elif types == {"bytes"}:
                sql_type = "BLOB"
            results.append(
                {
                    **{
                        k: v
                        for k, v in profile.items()
                        if k not in {"unique", "integer_digits", "scale", "timezone_modes"}
                    },
                    "unique_count_lower_bound": len(profile["unique"]),
                    "sql_type": sql_type,
                    "type_candidates": sorted(types),
                    "timezone_modes": sorted(profile["timezone_modes"]),
                }
            )
        return results
