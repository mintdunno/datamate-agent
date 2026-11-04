"""Evaluator-owned selection with label-free question and source snapshots."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from datamate.contracts.inputs import InputSnapshot, Question
from datamate.evaluation.io import load_reference_file, read_records


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _category(text: str, answer_type: str) -> str:
    text = text.casefold()
    if answer_type == "unanswerable":
        return "unanswerable"
    for name, terms in (
        ("ranking", ("top", "rank", "highest", "lowest", "cao nhất", "thấp nhất", "xếp hạng")),
        ("dates", ("date", "month", "year", "ngày", "tháng", "năm")),
        ("joins", ("each", "per customer", "từng", "mỗi khách", "kết hợp")),
        ("rules", ("eligible", "policy", "rule", "điều kiện", "quy định", "chính sách")),
        ("aggregation", ("total", "sum", "average", "count", "tổng", "trung bình", "bao nhiêu")),
    ):
        if any(term in text for term in terms):
            return name
    return "other"


def _difficulty(text: str) -> int:
    text = text.casefold()
    return sum(
        term in text
        for term in (
            " and ",
            " or ",
            " each ",
            " và ",
            " hoặc ",
            " mỗi ",
            "trong khoảng",
            "so sánh",
        )
    ) + (len(text) > 180)


def _select(rows, size, seed, name, hard=False):
    # Cover rare types first. Matching keeps these initial turns on distinct
    # businesses whenever the pool permits, including overlapping rare types.
    by_type = defaultdict(list)
    for row in rows:
        by_type[row["type"]].append(row)
    types = sorted(by_type, key=lambda kind: (len(by_type[kind]), kind))
    business_domains = {row["question"].business_id: row["domain"] for row in rows}
    assignments = {}
    domain_counts = Counter()

    def match(kind, visited):
        businesses = {row["question"].business_id for row in by_type[kind]}
        for business in sorted(
            businesses,
            key=lambda business: (
                domain_counts[business_domains[business]],
                _hash(f"{seed}:{name}:{business}"),
            ),
        ):
            if business in visited:
                continue
            visited.add(business)
            previous = assignments.get(business)
            if previous is None or match(previous, visited):
                assignments[business] = kind
                return True
        return False

    for kind in types[:size]:
        match(kind, set())
        domain_counts = Counter(business_domains[business] for business in assignments)
    type_business = {kind: business for business, kind in assignments.items()}
    selected = []
    business_counts = Counter()
    type_counts = Counter()
    domain_counts = Counter()
    strata_counts = Counter()
    remaining = list(rows)

    def choose(candidates):
        row = min(
            candidates,
            key=lambda row: (
                business_counts[row["question"].business_id],
                type_counts[row["type"]],
                domain_counts[row["domain"]],
                strata_counts[(row["type"], row["category"])],
                -row["difficulty"] if hard else 0,
                _hash(f"{seed}:{name}:{row['question'].id}"),
            ),
        )
        selected.append(row)
        remaining.remove(row)
        business_counts[row["question"].business_id] += 1
        type_counts[row["type"]] += 1
        domain_counts[row["domain"]] += 1
        strata_counts[(row["type"], row["category"])] += 1

    for kind in types[:size]:
        candidates = by_type[kind]
        if kind in type_business:
            candidates = [
                row for row in candidates if row["question"].business_id == type_business[kind]
            ]
        choose(candidates)
    while remaining and len(selected) < size:
        choose(remaining)
    return selected


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_hashes(split_root: Path) -> dict[str, str]:
    """Reject links before traversal, then hash source bytes in bounded chunks."""
    files = {}

    def visit(path):
        if path.is_symlink() or path.is_junction():
            raise ValueError("source split cannot contain symlinks or junctions")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                visit(child)
        elif path.is_file():
            files[path.relative_to(split_root).as_posix()] = _file_sha256(path)

    visit(split_root)
    return files


def _snapshot_sources(files, businesses):
    # Match preparation.discovery's allowlist, never the evaluator's broad hash inventory.
    return {
        business: {
            name: digest
            for name, digest in files.items()
            if name in {"rulebook.md", "calendar.csv"}
            or (
                len(Path(name).parts) >= 3
                and Path(name).parts[:2] == ("data", business)
                and Path(name).suffix.lower() in {".csv", ".xlsx", ".sqlite", ".md"}
            )
        }
        for business in businesses
    }


def build_suites(
    split_root: Path,
    output_dir: Path,
    seed: int = 42,
    smoke_size: int = 12,
    development_size: int = 72,
    hard_size: int = 36,
    holdout_size: int = 72,
    *,
    allow_large: bool = False,
    business_domains: dict[str, str] | None = None,
) -> dict:
    """Create bounded snapshots, using reference IDs and types only for stratification."""
    sizes = dict(
        smoke=smoke_size, development=development_size, hard=hard_size, holdout=holdout_size
    )
    if any(type(size) is not int or size < 0 for size in sizes.values()):
        raise ValueError("suite sizes must be nonnegative integers")
    if not allow_large and any(size > 500 for size in sizes.values()):
        raise ValueError("suite sizes above 500 require allow_large=True")
    split_root = Path(split_root)
    output_dir = Path(output_dir)
    if output_dir.resolve().is_relative_to(split_root.resolve()):
        raise ValueError("output directory must be outside the source split")
    files = _source_hashes(split_root)
    domains = dict(business_domains or {})
    if any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in domains.items()
    ):
        raise ValueError("business domains must map IDs to nonempty strings")
    question_path = split_root / "questions.jsonl"
    reference_path = split_root / "answers.jsonl"
    with question_path.open(encoding="utf-8-sig") as stream:
        raw = read_records(stream)
    references = load_reference_file(reference_path)
    if raw.issues or references.issues or raw.invalid_ids or references.invalid_ids:
        raise ValueError("invalid or duplicate input records")
    if raw.records.keys() != references.records.keys():
        raise ValueError("question and reference IDs differ")
    questions = [Question.model_validate(record) for record in raw.records.values()]
    valid_types = {
        "number",
        "percent",
        "string",
        "date",
        "boolean",
        "list",
        "table",
        "unanswerable",
    }
    rows = []
    for question in questions:
        answer_type = references.records[question.id].get("type")
        if not isinstance(answer_type, str) or answer_type not in valid_types:
            raise ValueError("invalid reference type")
        rows.append(
            dict(
                question=question,
                type=answer_type,
                category=_category(question.question, answer_type),
                difficulty=_difficulty(question.question),
                domain=domains.get(question.business_id, "unknown"),
            )
        )
    holdout_businesses = {
        q.business_id
        for q in questions
        if int(_hash(f"{seed}:business:{q.business_id}"), 16) % 100 < 20
    }
    pools = {
        "development": [r for r in rows if r["question"].business_id not in holdout_businesses],
        "holdout": [r for r in rows if r["question"].business_id in holdout_businesses],
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing_manifest.get("source_sha256") != files:
            raise ValueError("existing manifest source hashes differ; use a new output directory")
    manifest = {
        "version": 2,
        "seed": seed,
        "source_sha256": files,
        "strata": "business, supplied domain, reference type and lexical category",
        "domain_source": "caller-supplied mapping; unknown when absent",
        "business_domains": dict(sorted(domains.items())),
        "difficulty": "lexical heuristic, not a true label",
        "suites": {},
    }
    paths = {}
    snapshots = {}
    artifacts = {}
    for name, size in sizes.items():
        pool = pools["holdout" if name == "holdout" else "development"]
        chosen = _select(pool, size, seed, name, hard=name == "hard")
        payload = "".join(
            json.dumps(row["question"].model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            + "\n"
            for row in chosen
        )
        path = output_dir / f"{name}.jsonl"
        artifacts[path] = payload
        paths[name] = path
        business_ids = sorted({r["question"].business_id for r in chosen})
        snapshot = InputSnapshot(
            questions_sha256=_hash(payload), sources=_snapshot_sources(files, business_ids)
        )
        snapshot_path = path.with_suffix(".snapshot.json")
        artifacts[snapshot_path] = (
            json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        snapshots[name] = snapshot_path
        manifest["suites"][name] = {
            "requested": size,
            "available": len(pool),
            "selected": len(chosen),
            "shortage": max(0, size - len(chosen)),
            "sha256": _hash(payload),
            "business_ids": sorted({r["question"].business_id for r in chosen}),
            "available_business_ids": sorted({r["question"].business_id for r in pool}),
            "available_domains": dict(sorted(Counter(r["domain"] for r in pool).items())),
            "domains": dict(sorted(Counter(r["domain"] for r in chosen).items())),
            "available_answer_types": dict(sorted(Counter(r["type"] for r in pool).items())),
            "missing_available_types": sorted(
                {r["type"] for r in pool} - {r["type"] for r in chosen}
            ),
            "answer_types": dict(sorted(Counter(r["type"] for r in chosen).items())),
            "lexical_categories": dict(sorted(Counter(r["category"] for r in chosen).items())),
            "heuristic_difficulty": dict(
                sorted(Counter(str(r["difficulty"]) for r in chosen).items())
            ),
        }
    manifest["fingerprint"] = _hash(json.dumps(manifest, sort_keys=True, ensure_ascii=False))
    # Preflight every suite before writing anything, so rejected rebuilds leave
    # existing artifacts untouched. Missing safe artifacts may be added on replay.
    for path, payload in artifacts.items():
        if path.exists() and path.read_bytes() != payload.encode("utf-8"):
            raise ValueError(
                f"existing suite artifact differs: {path.name}; use a new output directory"
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in artifacts.items():
        if not path.exists():
            path.write_text(payload, encoding="utf-8", newline="\n")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"paths": paths, "snapshots": snapshots, "manifest": manifest}
