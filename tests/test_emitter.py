import os
from pathlib import Path

import pytest

from skilldistill.distill import SkillDraft
from skilldistill.emitter import (
    InvalidSkillNameError,
    SkillExistsError,
    UnsafeSkillPathError,
    write_skill,
)


def _draft(name: str = "safe-skill", content: str = "# Safe skill\n") -> SkillDraft:
    return SkillDraft(
        name=name,
        description="Use this skill when testing safe output.",
        content=content,
        origin="offline",
    )


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "../../outside",
        "/absolute",
        "nested/skill",
        ".",
        "-leading",
        "trailing-",
        "double--hyphen",
        "Uppercase",
        "contains space",
        "a" * 65,
        "con",
    ],
)
def test_write_skill_rejects_unsafe_or_nonportable_names(tmp_path: Path, name: str):
    skills_dir = tmp_path / "skills"

    with pytest.raises(InvalidSkillNameError):
        write_skill(_draft(name=name), skills_dir)

    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "outside").exists()


def test_write_skill_rejects_symlinked_skill_directory(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    outside = tmp_path / "outside"
    skills_dir.mkdir()
    outside.mkdir()
    try:
        (skills_dir / "safe-skill").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(UnsafeSkillPathError):
        write_skill(_draft(), skills_dir, force=True)

    assert not (outside / "SKILL.md").exists()


def test_write_skill_rejects_symlinked_target_file(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "safe-skill"
    outside = tmp_path / "outside.md"
    skill_dir.mkdir(parents=True)
    outside.write_text("do not replace", encoding="utf-8")
    try:
        (skill_dir / "SKILL.md").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    with pytest.raises(UnsafeSkillPathError):
        write_skill(_draft(content="replacement"), skills_dir, force=True)

    assert outside.read_text(encoding="utf-8") == "do not replace"


def test_write_skill_refuses_to_overwrite_without_force(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    target = write_skill(_draft(content="original"), skills_dir)

    with pytest.raises(SkillExistsError):
        write_skill(_draft(content="replacement"), skills_dir)

    assert target.read_text(encoding="utf-8") == "original"


def test_write_skill_accepts_relative_skills_directory(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    target = write_skill(_draft(content="relative"), "skills")

    assert target == (tmp_path / "skills" / "safe-skill" / "SKILL.md").resolve()
    assert target.read_text(encoding="utf-8") == "relative"


def test_force_write_atomically_replaces_existing_file(tmp_path: Path, monkeypatch):
    from skilldistill import emitter

    skills_dir = tmp_path / "skills"
    target = write_skill(_draft(content="original"), skills_dir)
    real_replace = os.replace
    observed = {}

    def checked_replace(source, destination):
        source = Path(source)
        destination = Path(destination)
        observed["temporary"] = source
        assert source.parent == destination.parent
        assert source.read_text(encoding="utf-8") == "replacement"
        assert destination.read_text(encoding="utf-8") == "original"
        real_replace(source, destination)

    monkeypatch.setattr(emitter.os, "replace", checked_replace)

    result = write_skill(_draft(content="replacement"), skills_dir, force=True)

    assert result == target
    assert target.read_text(encoding="utf-8") == "replacement"
    assert not observed["temporary"].exists()


def test_failed_atomic_replace_preserves_original_and_cleans_temp(tmp_path: Path, monkeypatch):
    from skilldistill import emitter

    skills_dir = tmp_path / "skills"
    target = write_skill(_draft(content="original"), skills_dir)

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(emitter.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_skill(_draft(content="replacement"), skills_dir, force=True)

    assert target.read_text(encoding="utf-8") == "original"
    assert list(target.parent.glob(".SKILL.md.*.tmp")) == []
