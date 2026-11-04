"""Exercise runtime imports in a fresh interpreter that refuses evaluator access."""

import subprocess
import sys

from datamate.evaluation.io import load_reference_file


def test_runtime_imports_and_serialization_work_when_evaluation_is_blocked():
    script = """
import importlib.abc
import sys

class BlockEvaluation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("datamate.evaluation"):
            raise RuntimeError("runtime attempted evaluator import")
        return None

sys.meta_path.insert(0, BlockEvaluation())
from datamate.contracts.inputs import Question
from datamate.contracts.answers import parse_submission, serialize_submission
from datamate.observability import RunMetadata
from datamate.preparation.pipeline import prepare_business
question = Question(id="q", business_id="shop", question="Số đơn?", as_of_date="2025-01-01")
answer = parse_submission({"id": question.id, "type": "abstain"})
assert serialize_submission(answer) == '{"id":"q","type":"abstain"}'
assert not any(name.startswith("datamate.evaluation") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_only_explicit_evaluator_reader_opens_synthetic_reference_file(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text('{"id":"demo","type":"number","answer":3,"alias":[]}\n', encoding="utf-8")
    batch = load_reference_file(path)
    assert batch.records["demo"]["answer"] == 3
    assert not batch.issues
