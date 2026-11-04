"""Shared local/cloud CLI. Evaluator imports occur only in evaluator commands."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _run(args):
    from datamate.contracts.inputs import InputSnapshot
    from datamate.inference.budget import RequestBudget
    from datamate.inference.client import inference_model
    from datamate.inference.config import gpu_preflight, is_local_backend, load_profile
    from datamate.inference.telemetry import GPUMonitor
    from datamate.preparation.discovery import sha256_file
    from datamate.runtime.batch import run_batch
    from datamate.runtime.inputs import PreparationPlans, read_questions

    profile = load_profile(args.profile)
    if profile.runtime == "nim" and args.split_id != "train":
        raise ValueError("NIM cloud is restricted to authorized training development inputs")
    if args.offline:
        from urllib.parse import urlsplit

        if urlsplit(profile.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("offline mode requires an explicitly selected loopback model server")
        if profile.runtime == "nim":
            raise ValueError("NIM cloud is development-only and cannot run offline")
    if profile.runtime in {"vllm", "sglang", "llama.cpp"}:
        gpu_preflight(profile)
    questions = read_questions(
        args.suite, maximum=args.max_questions, allow_large=args.allow_full_evaluation
    )
    snapshot_path = args.snapshot or args.suite.with_suffix(".snapshot.json")
    snapshot = None
    if snapshot_path.is_file() or args.snapshot is not None:
        snapshot = InputSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
        if snapshot.questions_sha256 != sha256_file(args.suite):
            raise ValueError("question suite differs from fixed snapshot")
    plans = (
        PreparationPlans.model_validate_json(args.plans.read_text(encoding="utf-8"))
        if args.plans
        else None
    )
    run_file = args.output_dir / "run.json"
    run_id = (
        json.loads(run_file.read_text(encoding="utf-8"))["run_id"]
        if args.resume and run_file.is_file()
        else args.run_id or str(uuid4())
    )
    ledger = args.ledger or args.state_root.parent / "nim_accounting.sqlite"
    if ledger.resolve().is_relative_to(args.split_root.resolve()):
        raise ValueError("API accounting must remain outside source split")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    budget = RequestBudget(
        ledger,
        run_id=run_id,
        rpm=args.rpm,
        daily_limit=args.daily_budget,
        run_limit=args.request_budget,
    )

    async def execute():
        async with inference_model(
            profile, budget, env_file=args.env_file, max_retries=args.retries, timeout=args.timeout
        ) as model:
            return await run_batch(
                questions,
                split_root=args.split_root,
                state_root=args.state_root,
                output_dir=args.output_dir,
                model=model,
                profile=profile,
                budget=budget,
                split_id=args.split_id,
                max_requests_per_question=args.question_requests,
                deadline=args.deadline,
                max_questions=args.max_questions,
                resume=args.resume,
                inference_settings={
                    "retries": args.retries,
                    "timeout": args.timeout,
                    "offline": args.offline,
                },
                allow_large=args.allow_full_evaluation,
                expected_sources=snapshot.sources if snapshot else None,
                normalization_plans=plans,
            )

    with GPUMonitor(enabled=is_local_backend(profile)) as monitor:
        result = asyncio.run(execute())
    result["gpu_telemetry"] = monitor.snapshot()
    result["accounting"] = budget.snapshot()
    observed_path = args.output_dir / "metadata.json"
    observed = (
        json.loads(observed_path.read_text(encoding="utf-8")) if observed_path.is_file() else {}
    )
    result["code_revision"] = observed.get("code_revision")
    result["code_dirty"] = observed.get("code_dirty")
    from datamate.runtime.batch import _write_run

    _write_run(args.output_dir / "run.json", result)
    _print(result)
    return 0 if result["status"] == "completed" else 2


def _serve(args):
    from datamate.inference.config import ResourceUnavailable, gpu_preflight, load_profile
    from datamate.inference.serving import serving_command

    profile = load_profile(args.profile)
    command = serving_command(
        profile, python_executable=args.runtime_python, host=args.host, port=args.port
    )
    if not args.launch:
        _print({"status": "plan_only", "command": command, "qualification": "unverified"})
        return 0
    if platform.system() != "Linux":
        raise ResourceUnavailable("vLLM launch requires Linux/WSL; no model was loaded")
    gpu_preflight(profile, force_local=True)
    if profile.model_path is None and not args.allow_model_download:
        raise ValueError("model download requires explicit --allow-model-download")
    if profile.model_path is not None and not Path(profile.model_path).is_dir():
        raise ValueError("selected local model snapshot directory is missing")
    environment = dict(os.environ)
    if not args.allow_model_download:
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return subprocess.run(command, env=environment, check=False).returncode


def _report(args):
    from datamate.evaluation.benchmarking import summarize_run
    from datamate.evaluation.io import JsonlRecords, load_reference_file
    from datamate.evaluation.scoring import EvaluationSpec
    from datamate.preparation.discovery import sha256_file
    from datamate.runtime.inputs import read_questions

    if args.run_dir.resolve().is_relative_to(args.rulebook.parent.resolve()):
        raise ValueError("evaluation reports must remain outside source split")
    questions = read_questions(
        args.suite, maximum=args.max_questions, allow_large=args.allow_full_evaluation
    )
    identifiers = {q.id for q in questions}
    all_references = load_reference_file(args.references)
    references = JsonlRecords(
        records={key: value for key, value in all_references.records.items() if key in identifiers},
        invalid_ids=all_references.invalid_ids & identifiers,
        issues=[
            issue for issue in all_references.issues if issue.question_id in identifiers | {None}
        ],
    )
    predictions = load_reference_file(args.run_dir / "submission.jsonl")
    metadata = json.loads((args.run_dir / "run.json").read_text(encoding="utf-8"))
    traces = [
        json.loads(line)
        for line in (args.run_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finals = [trace for trace in traces if trace["event"] == "question_finished"]
    source = {
        "questions": sha256_file(args.suite),
        "rulebook": sha256_file(args.rulebook),
        "references": sha256_file(args.references),
        "data": metadata["source_fingerprints"],
    }
    suite_fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    config = {
        "model": metadata["profile"],
        "harness": {
            "implementation": metadata.get("implementation", {}),
            "normalization_plans": metadata.get("configuration", {}).get("normalization_plans", {}),
            "context_packet_bytes": metadata.get("configuration", {}).get(
                "context_packet_bytes", 0
            ),
        },
        "prompt_sha256": metadata["prompt_sha256"],
        "data": metadata["source_fingerprints"],
        "dependencies": metadata.get("dependency_versions", {}),
        "inference_settings": metadata.get("configuration", {}).get("inference_settings", {}),
    }
    budget_policy = {
        **metadata.get("budget_policy", {}),
        "question_requests": metadata.get("configuration", {}).get("max_requests_per_question"),
        "question_deadline": metadata.get("configuration", {}).get("deadline"),
    }
    result = summarize_run(
        questions,
        references,
        predictions,
        EvaluationSpec(
            split_id=args.split_id, profile=args.scoring_profile, rulebook_sha256=source["rulebook"]
        ),
        suite_fingerprint=suite_fingerprint,
        configuration=config,
        latency_seconds=[trace["elapsed_ms"] / 1000 for trace in finals],
        tool_errors=metadata.get("tool_errors", 0),
        budgets=budget_policy,
        elapsed_seconds=max(metadata.get("elapsed_seconds", 0), 0.000001),
        error_categories=dict(
            Counter(trace["error_code"] for trace in finals if trace.get("error_code"))
        ),
    )
    result["api_consumption"] = metadata.get("accounting", {})
    result["run_status"] = metadata["status"]
    result["environment"] = {
        "platform": metadata.get("platform"),
        "python": metadata.get("python_version"),
        "gpu": metadata.get("gpu_telemetry", "not_measured"),
    }
    (args.run_dir / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print(result)
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="Plan local inference; launch/download are explicit opt-ins"
    )
    serve.add_argument("--profile", required=True, type=Path)
    serve.add_argument("--runtime-python", default="python")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--launch", action="store_true")
    serve.add_argument("--allow-model-download", action="store_true")
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--profile", required=True, type=Path)
    suites = commands.add_parser(
        "suites", help="Evaluator-owned fixed selection; no model requests"
    )
    suites.add_argument("--split-root", required=True, type=Path)
    suites.add_argument("--output-dir", required=True, type=Path)
    suites.add_argument("--seed", default=42, type=int)
    run = commands.add_parser("run")
    for name in ("profile", "suite", "split-root", "state-root", "output-dir"):
        run.add_argument(f"--{name}", required=True, type=Path)
    run.add_argument("--split-id", default="train")
    run.add_argument(
        "--plans", type=Path, help="Evidence-bound per-business TablePlan configuration"
    )
    run.add_argument(
        "--snapshot", type=Path, help="Solver-safe input binding; autodetected for fixed suites"
    )
    run.add_argument("--env-file", type=Path)
    run.add_argument("--ledger", type=Path)
    run.add_argument("--run-id")
    run.add_argument("--rpm", type=int, default=25)
    run.add_argument("--daily-budget", type=int, default=1200)
    run.add_argument("--request-budget", type=int, default=300)
    run.add_argument("--question-requests", type=int, default=8)
    run.add_argument("--max-questions", type=int, default=100)
    run.add_argument("--deadline", type=float, default=120)
    run.add_argument("--timeout", type=float, default=90)
    run.add_argument("--retries", type=int, default=2)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--offline", action="store_true")
    run.add_argument(
        "--allow-full-evaluation",
        action="store_true",
        help="Explicit large-run opt-in; automation still requires prior user approval",
    )
    report = commands.add_parser("report", help="Evaluator only; never called by the solver")
    for name in ("suite", "references", "rulebook", "run-dir"):
        report.add_argument(f"--{name}", required=True, type=Path)
    report.add_argument("--split-id", default="train")
    report.add_argument("--max-questions", type=int, default=500)
    report.add_argument("--allow-full-evaluation", action="store_true")
    report.add_argument(
        "--scoring-profile", choices=["global", "train_rulebook_v1"], default="global"
    )
    compare = commands.add_parser(
        "compare", help="Evaluator-only comparison with explicit attribution"
    )
    compare.add_argument("--left", required=True, type=Path)
    compare.add_argument("--right", required=True, type=Path)
    compare.add_argument("--axis", choices=["identical", "model", "harness"], default="identical")
    args = parser.parse_args(argv)
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "run":
            return _run(args)
        if args.command == "report":
            return _report(args)
        if args.command == "compare":
            from datamate.evaluation.benchmarking import compare_reports

            _print(
                compare_reports(
                    json.loads(args.left.read_text(encoding="utf-8")),
                    json.loads(args.right.read_text(encoding="utf-8")),
                    allow_model_difference=args.axis == "model",
                    allow_harness_difference=args.axis == "harness",
                )
            )
            return 0
        if args.command == "suites":
            from datamate.evaluation.suites import build_suites

            result = build_suites(args.split_root, args.output_dir, args.seed)
            _print(
                {
                    "manifest_fingerprint": result["manifest"]["fingerprint"],
                    "coverage": result["manifest"]["suites"],
                }
            )
        else:
            from datamate.inference.config import gpu_preflight, load_profile

            profile = load_profile(args.profile)
            _print(
                {
                    "profile": profile.name,
                    "resource_check": gpu_preflight(profile),
                    "qualification": "unverified; no inference performed",
                }
            )
        return 0
    except (ValueError, OSError) as error:
        from pydantic import ValidationError

        from datamate.inference.config import ResourceUnavailable

        # Validation messages can contain source values, so diagnostics stay content-free.
        _print(
            {
                "status": "failed",
                "error_code": type(error).__name__,
                "hint": "check paths, strict contracts, GPU requirements and run metadata",
                "message": str(error) if isinstance(error, ResourceUnavailable) else None,
                "validation_messages": [
                    issue["msg"]
                    for issue in error.errors(include_input=False, include_context=False)
                ]
                if isinstance(error, ValidationError)
                else [],
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
