"""Offline evaluator utilities; imported lazily by the central CLI only."""

import argparse
import json
import stat
import sys
from pathlib import Path

import duckdb
from pydantic import TypeAdapter

from datamate.evaluation import normalization as evaluation
from datamate.preparation.models import TableProfile


def _path(value: str | Path) -> Path:
    path = Path(value).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("linked paths are unsupported")
        if part.exists():
            metadata = part.stat()
            if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("reparse paths are unsupported")
            if part.is_file() and metadata.st_nlink > 1:
                raise ValueError("hard-linked files are unsupported")
    return path.resolve()


def _output(value: str | Path, split_root: Path) -> Path:
    path = _path(value)
    if path.is_relative_to(split_root) or "datasets" in {p.casefold() for p in path.parts}:
        raise ValueError("output must be outside source and dataset directories")
    if path.exists():
        raise FileExistsError("immutable output already exists")
    return path


def _source_paths(split_root: Path, sources: evaluation.SourceSnapshot) -> None:
    for files in sources.files.values():
        for name in files:
            path = _path(split_root / name)
            if not path.is_relative_to(split_root) or not path.is_file():
                raise ValueError("invalid frozen source path")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datamate normalization-eval")
    actions = parser.add_subparsers(dest="action", required=True)
    options = {
        "select": ("policy", "sources", "split-root", "output-dir"),
        "references": (
            "manifest",
            "selection",
            "proposals-root",
            "raw-root",
            "split-root",
            "suite",
        ),
        "freeze": ("baseline-result", "split-root", "output-dir"),
        "report": ("manifest", "suite", "policy"),
    }
    for action, flags in options.items():
        command = actions.add_parser(action)
        for flag in flags:
            command.add_argument(f"--{flag}", required=True)
        if action == "select":
            command.add_argument("--profiles", nargs="+", required=True)
        command.add_argument("--offline", action="store_true", help="always offline; no HTTP calls")
    return parser


def _select(args: argparse.Namespace) -> dict[str, object]:
    split = _path(args.split_root)
    output = _output(args.output_dir, split)
    policy = evaluation.load_pilot_policy(_path(args.policy))
    sources = evaluation.SourceSnapshot.model_validate_json(_path(args.sources).read_bytes())
    _source_paths(split, sources)
    current = evaluation.snapshot_sources(split, policy.business_ids, split_id=policy.split_id)
    if current != sources:
        raise ValueError("source snapshot drift")
    profiles = []
    for value in args.profiles:
        payload = json.loads(_path(value).read_text(encoding="utf-8"))
        profiles.extend(
            TypeAdapter(list[TableProfile]).validate_json(
                json.dumps(payload if isinstance(payload, list) else [payload])
            )
        )
    manifest = evaluation.select_cases(policy, sources, profiles)
    # A new directory claims this selection once; an existing frozen selection is never reused.
    output.mkdir(parents=True, exist_ok=False)
    digest = evaluation.write_frozen_selection(output / "selection.json", policy, sources)
    evaluation._write_once(output / "cases.json", manifest.model_dump_json().encode("utf-8"))
    return {"action": "select", "selection_digest": digest, "manifest_digest": manifest.digest}


def _references(args: argparse.Namespace) -> dict[str, object]:
    split = _path(args.split_root)
    suite = _output(args.suite, split)
    selection = _path(args.selection)
    payload = json.loads(selection.read_text(encoding="utf-8"))
    policy = evaluation.PilotPolicy.model_validate_json(json.dumps(payload["policy"]))
    sources = evaluation.SourceSnapshot.model_validate(payload["sources"])
    _source_paths(split, sources)
    proposals = _path(args.proposals_root)
    raw = _path(args.raw_root)
    for business in policy.business_ids:
        _path(proposals / business / "proposals.json")
        _path(raw / business / "raw.duckdb")
    result = evaluation.adjudicate_frozen_reference_files(
        _path(args.manifest), selection, proposals, raw, split, suite
    )
    return {"action": "references", "suite_digest": result.suite.digest}


def _freeze(args: argparse.Namespace) -> dict[str, object]:
    from datamate.evaluation.normalization_campaign import CampaignRunManifest, freeze_candidate

    split = _path(args.split_root)
    output = _output(args.output_dir, split)
    manifest = CampaignRunManifest.model_validate_json(_path(args.baseline_result).read_bytes())
    baseline = _path(manifest.artifact_dir)
    _path(baseline / "result.json")
    _path(baseline / "frozen-spec.json")
    frozen = freeze_candidate(manifest, output)
    return {"action": "freeze", "candidate_digest": frozen.sha256}


def _report(args: argparse.Namespace) -> dict[str, object]:
    policy = evaluation.load_pilot_policy(_path(args.policy))
    manifest = evaluation.CaseManifest.model_validate_json(_path(args.manifest).read_bytes())
    suite = evaluation.ReferenceSuite.model_validate_json(_path(args.suite).read_bytes())
    cases = {case.case_id: case for case in manifest.cases}
    if (
        suite.manifest_digest != manifest.digest
        or {r.case_id for r in suite.references} != cases.keys()
    ):
        raise ValueError("reference suite differs from manifest")
    for business in policy.business_ids:
        expected = "development" if business in policy.development else "checkpoint"
        selected = [c for c in manifest.cases if c.business_id == business]
        if len(selected) != 20 or any(c.partition != expected for c in selected):
            raise ValueError("manifest partitions differ from policy")
    development = [r for r in suite.references if cases[r.case_id].partition == "development"]
    counts = {
        "total_cases": len(development),
        "verified": sum(r.evidence_status == "VERIFIED" for r in development),
        "provisional": sum(r.evidence_status == "PROVISIONAL" for r in development),
        "unresolved": sum(r.evidence_status == "UNRESOLVED" for r in development),
        "verified_determinate": sum(r.determinate for r in development),
    }
    return {
        "action": "report",
        "manifest_digest": manifest.digest,
        "suite_digest": suite.digest,
        "development": counts,
        "checkpoint": "WITHHELD",
    }


def main(argv: list[str] | None = None) -> int:
    """Run supported evaluator actions offline, returning 2 on invalid/unsafe inputs."""
    args = _parser().parse_args(argv)
    handlers = {"select": _select, "references": _references, "freeze": _freeze, "report": _report}
    try:
        result = handlers[args.action](args)
    except (OSError, ValueError, KeyError, TypeError, duckdb.Error):
        # Validation errors may contain private proposal values, so never render exception text.
        print("normalization-eval: rejected invalid, changed, or unsafe artifacts", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0
