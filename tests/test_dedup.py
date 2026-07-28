import json
from pathlib import Path

import pytest

from skilldistill.cli import main
from skilldistill.dedup import find_overlaps


def _skill(root: Path, name: str, description: str, body: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_find_overlaps_ranks_related_skills_without_modifying_them(tmp_path):
    first = _skill(
        tmp_path,
        "review-python",
        "Use this skill when reviewing Python code for reliability.",
        "# Review\n\nInspect changed files. Run focused tests. Check error handling.",
    )
    second = _skill(
        tmp_path,
        "review-python-security",
        "Use this skill when reviewing Python code for security.",
        "# Review\n\nInspect changed files. Run focused tests. Check untrusted inputs.",
    )
    _skill(
        tmp_path,
        "release-notes",
        "Use this skill when drafting product release notes.",
        "# Publish\n\nCollect changes. Draft the announcement. Verify links.",
    )
    originals = {first: first.read_bytes(), second: second.read_bytes()}

    overlaps = find_overlaps(tmp_path, threshold=0.35)

    assert len(overlaps) == 1
    assert {overlaps[0].left_name, overlaps[0].right_name} == {
        "review-python",
        "review-python-security",
    }
    assert {"focused", "tests"} <= set(overlaps[0].shared_terms)
    assert {first: first.read_bytes(), second: second.read_bytes()} == originals


def test_overlaps_cli_supports_machine_readable_output(tmp_path, capsys):
    _skill(
        tmp_path,
        "one",
        "Use this skill when reviewing Python services.",
        "Inspect changed files. Run focused tests.",
    )
    _skill(
        tmp_path,
        "two",
        "Use this skill when reviewing Python workers.",
        "Inspect changed files. Run focused tests.",
    )

    assert main(["overlaps", str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["left_name"] == "one"
    assert payload[0]["right_name"] == "two"
    assert payload[0]["similarity"] >= 0.35


def test_find_overlaps_is_bounded_and_validates_arguments(tmp_path):
    _skill(tmp_path, "one", "Use this skill when doing one task.", "Check one result.")
    _skill(tmp_path, "two", "Use this skill when doing two tasks.", "Check two results.")
    _skill(tmp_path, "three", "Use this skill when doing three tasks.", "Check three results.")

    with pytest.raises(ValueError, match="found 3 skills"):
        find_overlaps(tmp_path, max_skills=2)
    with pytest.raises(ValueError, match="threshold"):
        find_overlaps(tmp_path, threshold=1.1)


def test_find_overlaps_does_not_report_shared_credentials(tmp_path):
    secret = "sk-proj-" + "x" * 24
    _skill(
        tmp_path,
        "one",
        "Use this skill when reviewing one service.",
        f"Inspect service configuration with {secret}.",
    )
    _skill(
        tmp_path,
        "two",
        "Use this skill when reviewing two services.",
        f"Inspect service configuration with {secret}.",
    )

    overlaps = find_overlaps(tmp_path, threshold=0.2)

    assert overlaps
    assert secret not in " ".join(overlaps[0].shared_terms)
