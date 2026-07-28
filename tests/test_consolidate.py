from pathlib import Path

import pytest

from skilldistill.consolidate import consolidate_skills, load_skill_source


def _write_skill(path: Path, name: str, description: str, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_offline_consolidation_deduplicates_exact_lines_and_preserves_unique_material(
    tmp_path,
):
    first = _write_skill(
        tmp_path / "first/SKILL.md",
        "review-python",
        "Use this skill when reviewing Python changes.",
        "# Review\n\n- Run focused tests.\n- Check exception handling.",
    )
    second = _write_skill(
        tmp_path / "second/SKILL.md",
        "review-security",
        "Use this skill when reviewing security-sensitive code.",
        "# Review\n\n- Run focused tests.\n- Check untrusted inputs.",
    )
    original = {first: first.read_bytes(), second: second.read_bytes()}

    forward = consolidate_skills([first, second], llm=None, name="review-code")
    reverse = consolidate_skills([second, first], llm=None, name="review-code")

    assert forward.content == reverse.content
    assert forward.mode == "consolidate"
    assert forward.source_count == 2
    repeated_section = forward.content.split("## Exact repeated-line candidates", 1)[1].split(
        "## Source records for review",
        1,
    )[0]
    assert repeated_section.count("- Run focused tests.") == 1
    assert forward.content.count("- Run focused tests.") == 3
    assert "Check exception handling" in forward.content
    assert "Check untrusted inputs" in forward.content
    assert {first: first.read_bytes(), second: second.read_bytes()} == original


def test_llm_consolidation_is_bounded_untrusted_and_normalized(tmp_path):
    secret = "sk-proj-" + "z" * 24
    first = _write_skill(
        tmp_path / "first/SKILL.md",
        "one",
        "Use this skill when doing one workflow.",
        f"# One\n\nNever expose {secret}.",
    )
    second = _write_skill(
        tmp_path / "second/SKILL.md",
        "two",
        "Use this skill when doing a related workflow.",
        "# Two\n\nKeep the unique branch.",
    )
    prompts = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return (
            "```markdown\n---\nname: ../../combined\n"
            "description: Related workflow.\n---\n\n"
            "# Combined\n\nKeep both branches.\n```"
        )

    draft = consolidate_skills([first, second], llm=fake_llm)

    assert "untrusted data" in prompts[0]
    assert secret not in prompts[0]
    assert "[REDACTED]" in prompts[0]
    assert draft.name == "combined"
    assert "../../combined" not in draft.content
    assert draft.description.startswith("Use this skill when")


def test_consolidation_requires_distinct_sources_and_rejects_oversized_skill(tmp_path):
    first = _write_skill(
        tmp_path / "first/SKILL.md",
        "one",
        "Use this skill when doing one workflow.",
        "# One",
    )
    with pytest.raises(ValueError, match="at least two distinct"):
        consolidate_skills([first, first])

    oversized = tmp_path / "large/SKILL.md"
    oversized.parent.mkdir()
    oversized.write_text("x" * 101, encoding="utf-8")
    with pytest.raises(ValueError, match="safety limit"):
        load_skill_source(oversized, max_chars=100)


def test_consolidation_accepts_duplicate_contents_as_a_cleanup_candidate(tmp_path):
    first = _write_skill(
        tmp_path / "first/SKILL.md",
        "one",
        "Use this skill when doing one workflow.",
        "# One\n\n- Verify the result.",
    )
    second = tmp_path / "second/SKILL.md"
    second.parent.mkdir()
    second.write_bytes(first.read_bytes())

    draft = consolidate_skills([first, second])

    assert draft.source_count == 2
    assert "Verify the result" in draft.content


def test_consolidation_preserves_redacted_source_metadata_for_review(tmp_path):
    first = tmp_path / "first/SKILL.md"
    first.parent.mkdir()
    first.write_text(
        "---\nname: one\n"
        "description: Use this skill when reviewing Python code.\n"
        "license: Apache-2.0\n"
        "allowed-tools:\n  - Read\n"
        "metadata:\n  api_key: secret-value\n"
        "---\n\n# Review\n\n- Run focused tests.\n",
        encoding="utf-8",
    )
    second = _write_skill(
        tmp_path / "second/SKILL.md",
        "two",
        "Use this skill when reviewing Python services.",
        "# Review\n\n- Run focused tests.",
    )

    draft = consolidate_skills([first, second])

    assert "license: Apache-2.0" in draft.content
    assert "allowed-tools:" in draft.content
    assert "api_key: '[REDACTED]'" in draft.content
    assert "secret-value" not in draft.content
