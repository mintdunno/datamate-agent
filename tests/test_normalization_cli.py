"""Offline evaluator CLI uses synthetic sources and never exposes reference contents."""

import json
import os
from pathlib import Path

import pytest
from normalization_fixtures import reference_world

from datamate.evaluation import normalization as api


@pytest.fixture
def world(tmp_path):
    policy, sources, profiles = reference_world(tmp_path)
    paths = {}
    for name, value in (("policy", policy), ("sources", sources)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(value.model_dump_json(), encoding="utf-8")
    paths["profiles"] = tmp_path / "profiles.json"
    paths["profiles"].write_text(
        json.dumps([p.model_dump(mode="json") for p in profiles]), encoding="utf-8"
    )
    paths["split"] = tmp_path / "train"
    paths["output"] = tmp_path / "evaluation"
    return policy, sources, profiles, paths


def select_args(paths):
    return [
        "select",
        "--policy",
        str(paths["policy"]),
        "--sources",
        str(paths["sources"]),
        "--profiles",
        str(paths["profiles"]),
        "--split-root",
        str(paths["split"]),
        "--output-dir",
        str(paths["output"]),
        "--offline",
    ]


def test_select_freezes_original_ids_once_without_reading_qa(world, monkeypatch, capsys):
    from datamate.evaluation.normalization_cli import main

    policy, sources, profiles, paths = world
    forbidden = paths["split"] / "answers.jsonl"
    forbidden.write_text("QA_SENTINEL", encoding="utf-8")
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == forbidden:
            raise AssertionError("QA read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert main(select_args(paths)) == 0
    manifest_path = paths["output"] / "cases.json"
    frozen = manifest_path.read_bytes()
    manifest = api.CaseManifest.model_validate_json(frozen)
    assert manifest.digest == api.select_cases(policy, sources, profiles).digest
    assert (paths["output"] / "selection.json").exists()
    assert main(select_args(paths)) == 2
    assert manifest_path.read_bytes() == frozen
    assert "QA_SENTINEL" not in capsys.readouterr().out


@pytest.mark.parametrize("location", ["source", "datasets"])
def test_select_refuses_source_and_dataset_outputs(world, location):
    from datamate.evaluation.normalization_cli import main

    *_, paths = world
    paths["output"] = (
        paths["split"] / "new-artifacts"
        if location == "source"
        else paths["split"].parent / "datasets" / "output"
    )
    assert main(select_args(paths)) == 2
    assert not paths["output"].exists()


def test_select_rejects_linked_inputs_before_creating_output(world):
    from datamate.evaluation.normalization_cli import main

    *_, paths = world
    linked = paths["policy"].with_name("linked-policy.json")
    os.link(paths["policy"], linked)
    paths["policy"] = linked
    assert main(select_args(paths)) == 2
    assert not paths["output"].exists()


def test_report_omits_labels_reasons_evidence_and_checkpoint_counts(world, capsys):
    from datamate.evaluation.normalization_cli import main

    policy, sources, profiles, paths = world
    manifest = api.select_cases(policy, sources, profiles)
    suite = api.freeze_reference_suite(manifest, [])
    payload = suite.model_dump(mode="json")
    for reference in payload["references"]:
        reference["expected_value"] = "PRIVATE_LABEL_SENTINEL"
        reference["reason"] = "PRIVATE_REASON_SENTINEL"
    manifest_path = paths["split"].parent / "manifest.json"
    suite_path = paths["split"].parent / "suite.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    suite_path.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        main(
            [
                "report",
                "--manifest",
                str(manifest_path),
                "--suite",
                str(suite_path),
                "--policy",
                str(paths["policy"]),
                "--offline",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["development"]["total_cases"] == 80
    assert report["development"]["unresolved"] == 80
    assert report["checkpoint"] == "WITHHELD"
    assert "PRIVATE" not in output
    assert "expected_value" not in output
    assert "case_ids" not in output


def test_invalid_input_errors_do_not_echo_content(world, capsys):
    from datamate.evaluation.normalization_cli import main

    *_, paths = world
    paths["policy"].write_text('{"PRIVATE_LABEL_SENTINEL": 1}', encoding="utf-8")
    assert main(select_args(paths)) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_LABEL_SENTINEL" not in captured.err + captured.out


def test_help_does_not_advertise_unsupported_execution(capsys):
    from datamate.evaluation.normalization_cli import main

    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "baseline" not in output and "verify" not in output


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def test_references_uses_frozen_files_and_does_not_overwrite(world, capsys):
    import duckdb

    from datamate.evaluation.normalization_cli import main
    from datamate.preparation.discovery import discover_business
    from datamate.preparation.loaders import load_tables
    from datamate.preparation.storage import create_storage, stage_table

    policy, _, _, paths = world
    assert main(select_args(paths)) == 0
    root = paths["split"].parent
    for business in policy.business_ids:
        proposal = root / "proposals" / business / "proposals.json"
        proposal.parent.mkdir(parents=True)
        proposal.write_text("[]", encoding="utf-8")
        raw = root / "raw" / business / "raw.duckdb"
        raw.parent.mkdir(parents=True)
        with duckdb.connect(str(raw)) as db:
            create_storage(db)
            inputs = discover_business(paths["split"], business, split_id="train")
            for source in inputs.sources:
                for table in load_tables(source.path):
                    stage_table(db, source, table)
    suite_path = root / "suite.json"
    args = [
        "references",
        "--manifest",
        str(paths["output"] / "cases.json"),
        "--selection",
        str(paths["output"] / "selection.json"),
        "--proposals-root",
        str(root / "proposals"),
        "--raw-root",
        str(root / "raw"),
        "--split-root",
        str(paths["split"]),
        "--suite",
        str(suite_path),
        "--offline",
    ]
    assert main(args) == 0
    frozen = suite_path.read_bytes()
    assert len(api.ReferenceSuite.model_validate_json(frozen).references) == 100
    assert main(args) == 2
    assert suite_path.read_bytes() == frozen
    assert "expected_value" not in capsys.readouterr().out


def test_freeze_validates_completed_baseline_and_preserves_write_once_output(world, capsys):
    from datamate.evaluation import normalization_campaign as campaign
    from datamate.evaluation.normalization_cli import main

    policy, sources, profiles, paths = world
    manifest = api.select_cases(policy, sources, profiles)
    suite = api.freeze_reference_suite(manifest, [])

    def prepare(*, business_id, cases, **kwargs):
        return campaign.BusinessPreparation(
            business_id=business_id,
            audit_status="PASS",
            full_cpu_audit=True,
            cache_mode="COLD",
            accepted_plan_reuse=False,
            measurements_complete=True,
            elapsed_seconds=1.0,
            peak_rss_bytes=1024,
            peak_disk_bytes=2048,
            api_attempts=0,
            input_tokens=0,
            output_tokens=0,
            case_results=tuple(
                campaign.CandidateCaseResult(
                    case_id=c.case_id, business_id=business_id, status="ABSTAINED", value=None
                )
                for c in cases
            ),
        )

    root = paths["split"].parent
    campaign.run_development_baseline(
        policy,
        suite,
        prepare,
        cases=manifest,
        sources=sources,
        split_root=paths["split"],
        output_dir=root / "baseline",
        config_hashes={k: "a" * 64 for k in campaign.CONFIG_KEYS},
        candidate_id="synthetic",
    )
    args = [
        "freeze",
        "--baseline-result",
        str(root / "baseline" / "result.json"),
        "--split-root",
        str(paths["split"]),
        "--output-dir",
        str(root / "final"),
        "--offline",
    ]
    assert main(args) == 0
    frozen = (root / "final" / "frozen-spec.json").read_bytes()
    assert main(args) == 2
    assert (root / "final" / "frozen-spec.json").read_bytes() == frozen
    assert "expected_value" not in capsys.readouterr().out


def test_report_rejects_policy_partition_relabeling(world, capsys):
    from datamate.evaluation.normalization_cli import main

    policy, sources, profiles, paths = world
    manifest = api.select_cases(policy, sources, profiles)
    suite = api.freeze_reference_suite(manifest, [])
    changed = policy.model_copy(update={"development": ("a", "b", "c", "e"), "checkpoint": ("d",)})
    paths["policy"].write_text(changed.model_dump_json(), encoding="utf-8")
    root = paths["split"].parent
    (root / "cases.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (root / "suite.json").write_text(suite.model_dump_json(), encoding="utf-8")
    assert (
        main(
            [
                "report",
                "--manifest",
                str(root / "cases.json"),
                "--suite",
                str(root / "suite.json"),
                "--policy",
                str(paths["policy"]),
            ]
        )
        == 2
    )
    assert capsys.readouterr().out == ""


def test_select_refuses_drift_before_writing_any_artifacts(world):
    from datamate.evaluation.normalization_cli import main

    *_, paths = world
    source = paths["split"] / "data" / "a" / "sales.csv"
    source.write_text("id,amount\n1,CHANGED\n", encoding="utf-8")
    assert main(select_args(paths)) == 2
    assert not paths["output"].exists()


@pytest.fixture
def central_cli(tmp_path):
    import subprocess
    import sys

    guard_root = tmp_path / "subprocess-guard"
    guard_root.mkdir()
    marker = guard_root / "active"
    (guard_root / "sitecustomize.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "class OfflineEvaluatorOnly:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        forbidden = ('datamate.inference', 'datamate.runtime', "
        "'pydantic_ai', 'openai', 'httpx')\n"
        "        if any(fullname == name or fullname.startswith(name + '.') "
        "for name in forbidden):\n"
        "            raise AssertionError('inference/provider import attempted')\n"
        "def offline_audit(event, args):\n"
        "    if event in {'socket.connect', 'socket.getaddrinfo', "
        "'subprocess.Popen', 'os.system'}:\n"
        "        raise AssertionError('network or child execution attempted')\n"
        "sys.meta_path.insert(0, OfflineEvaluatorOnly())\n"
        "sys.addaudithook(offline_audit)\n"
        f"Path({str(marker)!r}).write_text('active', encoding='utf-8')\n",
        encoding="utf-8",
    )
    source_root = Path(api.__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(guard_root), str(source_root))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }

    def invoke(*arguments):
        marker.unlink(missing_ok=True)
        completed = subprocess.run(
            [sys.executable, "-m", "datamate", *map(str, arguments)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert marker.read_text(encoding="utf-8") == "active"
        return completed

    return invoke


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (("--help",), "normalization-eval"),
        (("normalization-eval", "--help"), "select,references,freeze,report"),
        (("normalization-eval", "report", "--help"), "--manifest"),
    ],
)
def test_central_cli_dispatches_help_without_inference(central_cli, arguments, expected):
    completed = central_cli(*arguments)
    assert completed.returncode == 0, completed.stderr
    assert expected in completed.stdout
    assert completed.stderr == ""


def test_central_cli_dispatches_sanitized_report_without_inference(world, central_cli):
    policy, sources, profiles, paths = world
    manifest = api.select_cases(policy, sources, profiles)
    suite = api.freeze_reference_suite(manifest, []).model_dump(mode="json")
    for reference in suite["references"]:
        reference["expected_value"] = "SUBPROCESS_PRIVATE_LABEL"
        reference["reason"] = "SUBPROCESS_PRIVATE_REASON"
    root = paths["split"].parent
    manifest_path = root / "central-cases.json"
    suite_path = root / "central-suite.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    suite_path.write_text(json.dumps(suite), encoding="utf-8")

    completed = central_cli(
        "normalization-eval",
        "report",
        "--manifest",
        manifest_path,
        "--suite",
        suite_path,
        "--policy",
        paths["policy"],
        "--offline",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["action"] == "report"
    assert report["manifest_digest"] == manifest.digest
    assert report["development"] == {
        "total_cases": 80,
        "verified": 0,
        "provisional": 0,
        "unresolved": 80,
        "verified_determinate": 0,
    }
    assert report["checkpoint"] == "WITHHELD"
    assert "SUBPROCESS_PRIVATE" not in completed.stdout + completed.stderr
    assert "expected_value" not in completed.stdout
    assert "case_ids" not in completed.stdout
    assert completed.stderr == ""
