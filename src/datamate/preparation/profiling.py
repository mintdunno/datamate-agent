"""Bounded-memory profiles cover every record without retaining value samples."""

import hashlib
import heapq
import json
import re
from collections import Counter
from decimal import Decimal

import polars as pl
from duckdb import DuckDBPyConnection

from datamate.preparation.models import ColumnProfile, SampleRef, TableProfile
from datamate.preparation.normalization import PreparedRow, encoded
from datamate.preparation.storage import iter_raw_batches


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


def _shape(value: object) -> str:
    if not isinstance(value, str):
        return type(value).__name__
    text = re.sub(r"[^\W\d_]+", "a", value)
    text = re.sub(r"\d+", "9", text)
    return "text:" + text[:96]


def profile_table(db: DuckDBPyConnection, table_id: str, *, batch_size: int = 1000) -> TableProfile:
    info = db.execute(
        "SELECT columns_json,headers_json,declared_types_json,source,source_sha256,table_name "
        "FROM meta.source_tables WHERE table_id=?",
        [table_id],
    ).fetchone()
    if info is None:
        raise ValueError("unknown staged table")
    columns = json.loads(info[0])
    headers = dict(zip(columns, json.loads(info[1]), strict=True))
    declared = json.loads(info[2])
    declared_types = dict(zip(columns, declared, strict=True)) if declared else {}
    # Spill per-batch format counts to DuckDB; do not retain unbounded pattern dictionaries.
    db.execute("""CREATE TABLE IF NOT EXISTS meta.profile_patterns(
        table_id VARCHAR,column_id VARCHAR,pattern VARCHAR,n BIGINT,sample VARCHAR)""")
    db.execute("DELETE FROM meta.profile_patterns WHERE table_id=?", [table_id])
    kinds = Counter()
    representatives = []
    counters = [
        {"types": Counter(), "null": 0, "blank": 0, "unique": set(), "exact": True} for _ in columns
    ]
    for batch in iter_raw_batches(db, table_id, batch_size):
        kinds.update(record.kind for record in batch)
        data = [record for record in batch if record.kind == "data"]
        if not data:
            continue
        for record in data:
            rank = int(
                hashlib.sha256(("representative-v1:" + record.record_id).encode()).hexdigest(), 16
            )
            column_index = rank % len(columns)
            item = (
                -rank,
                record.record_id,
                columns[column_index],
                _shape(record.source.values[column_index]),
            )
            if len(representatives) < 32:
                heapq.heappush(representatives, item)
            elif item > representatives[0]:
                heapq.heapreplace(representatives, item)
        pattern_rows = []
        for index, column in enumerate(columns):
            stats = counters[index]
            values = [record.source.values[index] for record in data]
            stats["types"].update(
                dict(
                    pl.Series("kind", [type(v).__name__ for v in values]).value_counts().iter_rows()
                )
            )
            patterns = Counter()
            examples = {}
            for record, value in zip(data, values, strict=True):
                stats["null"] += value is None
                stats["blank"] += type(value) is str and value == ""
                digest = hashlib.sha256(
                    json.dumps(encoded(value), sort_keys=True).encode()
                ).digest()
                if len(stats["unique"]) < 1000:
                    stats["unique"].add(digest)
                elif digest not in stats["unique"]:
                    stats["exact"] = False
                shape = _shape(value)
                patterns[shape] += 1
                examples[shape] = min(examples.get(shape, record.record_id), record.record_id)
            pattern_rows.extend(
                (table_id, column, pattern, count, examples[pattern])
                for pattern, count in patterns.items()
            )
        db.executemany("INSERT INTO meta.profile_patterns VALUES (?,?,?,?,?)", pattern_rows)
    grouped = (
        "SELECT column_id,pattern,sum(n) AS n,min(sample) AS sample "
        "FROM meta.profile_patterns WHERE table_id=? GROUP BY column_id,pattern"
    )
    profiles = []
    for column, stats in zip(columns, counters, strict=True):
        formats = db.execute(
            "SELECT pattern,n FROM (" + grouped + ") WHERE column_id=? "
            "ORDER BY n DESC,pattern LIMIT 128",
            [table_id, column],
        ).fetchall()
        count = db.execute(
            "SELECT count(*) FROM (" + grouped + ") WHERE column_id=?", [table_id, column]
        ).fetchone()[0]
        profiles.append(
            ColumnProfile(
                column_id=column,
                source_header=headers[column],
                declared_type=declared_types.get(column),
                raw_types=dict(stats["types"]),
                null_count=stats["null"],
                blank_count=stats["blank"],
                unique_count_lower_bound=len(stats["unique"]),
                unique_count_exact=stats["exact"],
                format_counts=dict(formats),
                format_counts_truncated=count > 128,
                distinct_formats=count,
            )
        )
    candidates = {}
    for direction in ("ASC", "DESC"):
        for column, pattern, count, record in db.execute(
            grouped + f" ORDER BY n {direction},column_id,pattern LIMIT 16", [table_id]
        ).fetchall():
            key = column, pattern, record
            candidates[key] = SampleRef(
                record_id=record,
                column_id=column,
                pattern_id=hashlib.sha256(pattern.encode()).hexdigest(),
                pattern_count=count,
            )
    for _, record, column, pattern in representatives:
        count = db.execute(
            "SELECT sum(n) FROM meta.profile_patterns "
            "WHERE table_id=? AND column_id=? AND pattern=?",
            [table_id, column, pattern],
        ).fetchone()[0]
        candidates[column, pattern, record] = SampleRef(
            record_id=record,
            column_id=column,
            pattern_id=hashlib.sha256(pattern.encode()).hexdigest(),
            pattern_count=count,
        )
    result = TableProfile(
        table_id=table_id,
        source=info[3],
        source_sha256=info[4],
        source_table=info[5],
        row_count=kinds["data"],
        raw_record_count=sum(kinds.values()),
        record_kinds=dict(kinds),
        columns=tuple(profiles),
        samples=tuple(candidates[k] for k in sorted(candidates)),
        profile_version="profile-v2-2",
        candidate_keys=tuple(
            column.column_id
            for column in profiles
            if kinds["data"] > 0
            and column.unique_count_exact
            and column.unique_count_lower_bound == kinds["data"]
            and column.null_count == 0
            and column.blank_count == 0
        ),
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS meta.profiles(table_id VARCHAR PRIMARY KEY, profile JSON)"
    )
    db.execute(
        "INSERT OR REPLACE INTO meta.profiles VALUES (?,?)", [table_id, result.model_dump_json()]
    )
    return result


def select_evidence(profile: TableProfile, *, seed: int, max_samples: int) -> list[SampleRef]:
    if type(max_samples) is not int or max_samples < 0:
        raise ValueError("sample budget must be a nonnegative integer")

    def tie(sample):
        return hashlib.sha256(f"{seed}:{sample.record_id}:{sample.column_id}".encode()).hexdigest()

    rare = sorted(profile.samples, key=lambda s: (s.pattern_count, tie(s)))
    common = sorted(profile.samples, key=lambda s: (-s.pattern_count, tie(s)))
    selected = []
    seen = set()
    for pair in zip(rare, common, strict=True):
        for sample in pair:
            key = sample.record_id, sample.column_id, sample.pattern_id
            if len(selected) < max_samples and key not in seen:
                selected.append(sample)
                seen.add(key)
    return selected
