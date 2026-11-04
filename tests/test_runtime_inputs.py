import json
import subprocess
import sys

import pytest

from datamate.runtime.inputs import read_questions


def test_question_inputs_reject_references_duplicates_and_large_suites(tmp_path):
    path = tmp_path / "questions.jsonl"
    record = {"id": "q", "business_id": "biz", "question": "question", "as_of_date": "2026-01-01"}
    path.write_text(json.dumps(record) + "\n")
    assert len(read_questions(path)) == 1
    path.write_text(json.dumps({**record, "answer": 42}) + "\n")
    with pytest.raises(ValueError):
        read_questions(path)
    path.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")
    with pytest.raises(ValueError):
        read_questions(path)
    with pytest.raises(ValueError):
        read_questions(path, maximum=1)
    path.write_text('{"id":"q","id":"other"}\n')
    with pytest.raises(ValueError):
        read_questions(path)


def test_runtime_imports_no_evaluator_modules():
    code = (
        "import datamate.runtime.solver,datamate.runtime.inputs; "
        "import datamate.runtime.batch,datamate.__main__,sys; "
        "assert not any(k.startswith('datamate.evaluation') for k in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


def test_large_limits_require_explicit_opt_in(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(
        json.dumps({"id": "q", "business_id": "b", "question": "x", "as_of_date": "2026-01-01"})
        + "\n"
    )
    with pytest.raises(ValueError):
        read_questions(path, maximum=3000)
    assert len(read_questions(path, maximum=3000, allow_large=True)) == 1
