"""Bounded question-only input reading; evaluator labels are rejected by strict contracts."""

import json
from pathlib import Path

from datamate.contracts.inputs import Question


def _unique(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate JSON field")
        values[key] = value
    return values


def read_questions(path: Path, *, maximum: int = 100) -> list[Question]:
    if not 1 <= maximum <= 500:
        raise ValueError("evaluation size must be 1..500; full evaluation is not enabled")
    result, identifiers = [], set()
    with path.open(encoding="utf-8-sig") as stream:
        for index, line in enumerate(iter(lambda: stream.readline(64001), "")):
            if index >= maximum:
                raise ValueError("suite exceeds configured question limit")
            if len(line.encode()) > 64000:
                raise ValueError("question record exceeds input byte limit")
            question = Question.model_validate(json.loads(line, object_pairs_hook=_unique))
            if question.id in identifiers:
                raise ValueError("duplicate question ID")
            identifiers.add(question.id)
            result.append(question)
    if not result:
        raise ValueError("suite is empty")
    return result
