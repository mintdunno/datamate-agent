"""Populate Stage 1 run contracts from observed source and Git identities."""

import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from datamate.observability import ModelMetadata, RunMetadata


def collect_metadata(
    *, run_id, split_id, sources, configuration_sha256, prompt_sha256, profile, dependency_versions
) -> RunMetadata | None:
    root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(root), "status", "--porcelain"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    rulebooks = {files["rulebook.md"] for files in sources.values()}
    calendars = {files["calendar.csv"] for files in sources.values()}
    if len(rulebooks) != 1 or len(calendars) != 1:
        raise ValueError("split rulebook/calendar fingerprints are inconsistent")
    return RunMetadata(
        run_id=run_id,
        started_at=datetime.now(UTC),
        split_id=split_id,
        inputs_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        rulebook_sha256=next(iter(rulebooks)),
        calendar_sha256=next(iter(calendars)),
        configuration_sha256=configuration_sha256,
        code_revision=revision,
        code_dirty=dirty,
        python_version=platform.python_version(),
        platform=platform.platform(),
        dependency_versions=dependency_versions,
        prompt_sha256=prompt_sha256,
        model=ModelMetadata(
            provider=profile.runtime, model_id=profile.model_id, revision=profile.revision
        ),
    )
