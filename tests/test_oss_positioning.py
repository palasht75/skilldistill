from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()

TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {"LICENSE"}
GENERATED_DIRS = {
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "skill-drafts",
    "venv",
}
GENERATED_PATHS = {
    Path("evaluation/artifacts"),
    Path("evaluation/results"),
}


def _is_excluded(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if path == THIS_FILE:
        return True
    if path.name == ".env" or path.name.startswith(".env."):
        return True
    if any(part in GENERATED_DIRS or part.endswith(".egg-info") for part in relative.parts):
        return True
    return any(relative == prefix or prefix in relative.parents for prefix in GENERATED_PATHS)


def _authored_text_files() -> list[Path]:
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(ROOT):
        current = Path(directory)
        dirnames[:] = [
            name for name in dirnames if not _is_excluded(current / name)
        ]
        for filename in filenames:
            path = current / filename
            if _is_excluded(path):
                continue
            if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_FILENAMES:
                files.append(path)
    return sorted(files)


def test_project_text_keeps_open_source_positioning():
    prohibited = {
        "internal-event framing": re.compile(
            r"\b" + "inno" + "vation" + r"[\s_-]*" + "we" + "ek" + r"\b",
            re.IGNORECASE,
        ),
        "organization-pilot framing": re.compile(
            r"\b" + "corpo" + "rate" + r"[\s_-]*" + "pilot" + r"\b",
            re.IGNORECASE,
        ),
        "workplace framing": re.compile(
            r"\b" + "employ" + "er" + r"[\s_-]*(?:specific|context|initiative)\b",
            re.IGNORECASE,
        ),
        "private-event framing": re.compile(
            r"\b" + "internal" + r"[\s_-]*(?:event|" + "hack" + "athon" + r")\b",
            re.IGNORECASE,
        ),
        "unsupported monetary promise": re.compile(
            r"\b"
            + r"sav(?:e|es|ed|ing)"
            + r"\s+(?:\w+\s+){0,2}"
            + "thou"
            + "sands"
            + r"\b",
            re.IGNORECASE,
        ),
    }

    matches = []
    files = _authored_text_files()
    assert ROOT / "README.md" in files
    assert ROOT / "pyproject.toml" in files

    for path in files:
        text = path.read_text(encoding="utf-8")
        for label, pattern in prohibited.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                matches.append(f"{path.relative_to(ROOT)}:{line}: {label}")

    assert not matches, "Remove non-project positioning from authored text:\n" + "\n".join(matches)
