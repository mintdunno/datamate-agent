"""Allowlisted discovery never reads split-root questions, labels, or credentials."""

import hashlib
from pathlib import Path

from datamate.preparation.models import BusinessInputs, SourceFile

SOURCE_SUFFIXES = {".csv", ".xlsx", ".sqlite"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_file(path: Path, root: Path) -> SourceFile:
    """Reject symlink/junction escapes before hashing any source bytes."""
    if path.is_symlink() or path.is_junction():
        raise ValueError("linked inputs are not allowed")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("input escapes allowed root")
    if not resolved.is_file():
        raise ValueError("expected source file")
    return SourceFile(resolved, resolved.relative_to(root).as_posix(), sha256_file(resolved))


def discover_business(split_root: Path, business_id: str, *, split_id: str) -> BusinessInputs:
    if (
        not business_id.strip()
        or business_id in {".", ".."}
        or any(char in business_id for char in "/\\:\x00")
    ):
        raise ValueError("business_id must be one safe path component")
    root = split_root.resolve(strict=True)
    data = root / "data"
    business = data / business_id
    for directory in [data, business]:
        if directory.is_symlink() or directory.is_junction():
            raise ValueError("linked input directories are not allowed")
        if not directory.resolve(strict=True).is_relative_to(root):
            raise ValueError("business escapes split")
    documents = [checked_file(root / name, root) for name in ("rulebook.md", "calendar.csv")]
    sources = []
    for path in sorted(business.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError("linked business inputs are not allowed")
        if path.is_dir():
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES:
            sources.append(checked_file(path, root))
        elif path.suffix.lower() == ".md":
            documents.append(checked_file(path, root))
        else:
            raise ValueError(f"unsupported business input: {path.name}")
    if not sources:
        raise ValueError("business has no supported sources")
    return BusinessInputs(
        split_id, business_id, root, business.resolve(), tuple(sources), tuple(documents)
    )
