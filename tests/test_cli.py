from skilldistill.cli import main


def test_scan_ranks_good_over_threshold(good_session, capsys):
    assert main(["scan", str(good_session.parent), "--min-score", "0.4"]) == 0
    out = capsys.readouterr().out
    assert "good.jsonl" in out and "goal:" in out


def test_distill_offline_end_to_end(good_session, tmp_path, capsys):
    rc = main(
        ["distill", str(good_session), "--offline", "--skills-dir", str(tmp_path / "skills")]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "SKILL.md" in out and "offline" in out


def test_scan_missing_dir_exit_2(tmp_path):
    assert main(["scan", str(tmp_path / "nope")]) == 2


def test_scan_rejects_a_file_root(tmp_path):
    path = tmp_path / "one.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    assert main(["scan", str(path)]) == 2


def test_scan_validates_threshold_and_limit(tmp_path):
    assert main(["scan", str(tmp_path), "--min-score", "1.1"]) == 2
    assert main(["scan", str(tmp_path), "--limit", "0"]) == 2


def test_distill_rejects_a_directory_as_a_session(tmp_path):
    assert main(["distill", str(tmp_path)]) == 2


def test_distill_rejects_a_missing_comparison_directory(
    good_session,
    tmp_path,
):
    assert (
        main(
            [
                "distill",
                str(good_session),
                "--compare-dir",
                str(tmp_path / "missing"),
            ]
        )
        == 2
    )


def test_scan_cursor_exported_markdown(tmp_path, capsys):
    export = tmp_path / "cursor-chat.md"
    export.write_text(
        "## User\n\nFix the flaky retry logic\n\n"
        "## Assistant\n\nImplemented the fix. 8 tests passed.",
        encoding="utf-8",
    )

    assert main(["scan", str(tmp_path), "--source", "cursor", "--min-score", "0.2"]) == 0
    assert "cursor-chat.md" in capsys.readouterr().out


def test_distill_defaults_to_neutral_review_directory_and_stays_offline(
    good_session,
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")

    assert main(["distill", str(good_session)]) == 0

    output = capsys.readouterr().out
    assert "origin: offline" in output
    assert list((tmp_path / "skill-drafts").glob("*/SKILL.md"))
    assert not (tmp_path / ".cursor").exists()


def test_distill_compares_candidate_against_separate_existing_library(
    good_session,
    tmp_path,
    capsys,
):
    existing = tmp_path / "existing/retry-workflow/SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\nname: retry-workflow\n"
        "description: Use this skill when repairing retry behavior.\n"
        "---\n\n# Existing\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "distill",
                str(good_session),
                "--name",
                "retry-workflow",
                "--skills-dir",
                str(tmp_path / "drafts"),
                "--compare-dir",
                str(tmp_path / "existing"),
            ]
        )
        == 0
    )

    assert "similar existing skill: retry-workflow" in capsys.readouterr().err


def test_distill_explicit_unconfigured_provider_fails_closed(
    good_session,
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert (
        main(
            [
                "distill",
                str(good_session),
                "--provider",
                "openai",
                "--skills-dir",
                str(tmp_path / "skills"),
            ]
        )
        == 2
    )
    assert "not configured" in capsys.readouterr().err
    assert not (tmp_path / "skills").exists()


def test_distill_multiple_sessions_into_one_candidate(good_session, tmp_path, capsys):
    second = tmp_path / "second.jsonl"
    second.write_text(
        good_session.read_text(encoding="utf-8").replace("payment", "invoice"),
        encoding="utf-8",
    )
    output = tmp_path / "skills"

    assert (
        main(
            [
                "distill",
                str(good_session),
                str(second),
                "--offline",
                "--name",
                "retry-workflow",
                "--skills-dir",
                str(output),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    content = (output / "retry-workflow/SKILL.md").read_text(encoding="utf-8")
    assert "sources: 2" in stdout
    assert "Shared workflow evidence" in content


def test_goal_override_must_be_repeated_for_each_session(good_session, tmp_path, capsys):
    second = tmp_path / "second.jsonl"
    second.write_text(good_session.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["distill", str(good_session), str(second), "--goal", "Fix retries"]) == 2
    assert "repeat --goal exactly once per session" in capsys.readouterr().err


def test_goal_override_supports_multiple_cursor_streams(tmp_path, capsys):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(
        '{"type":"assistant","message":{"content":"First complete."}}\n',
        encoding="utf-8",
    )
    second.write_text(
        '{"type":"assistant","message":{"content":"Second complete."}}\n',
        encoding="utf-8",
    )
    output = tmp_path / "drafts"

    assert (
        main(
            [
                "distill",
                str(first),
                str(second),
                "--goal",
                "Repair retry handling in the API client",
                "--goal",
                "Repair retry handling in the worker",
                "--name",
                "retry-handling",
                "--skills-dir",
                str(output),
            ]
        )
        == 0
    )

    content = (output / "retry-handling/SKILL.md").read_text(encoding="utf-8")
    assert "tasks involving handling, retry" in content
    assert "No goal terms reached" not in content


def test_consolidate_cli_writes_draft_without_modifying_sources(tmp_path, capsys):
    first = tmp_path / "skills/review-code/SKILL.md"
    first.parent.mkdir(parents=True)
    first.write_text(
        "---\nname: review-code\n"
        "description: Use this skill when reviewing Python code.\n---\n\n"
        "# Review code\n\n- Run focused tests.\n- Check error handling.",
        encoding="utf-8",
    )
    second = tmp_path / "skills/code-security-review/SKILL.md"
    second.parent.mkdir(parents=True)
    second.write_text(
        "---\nname: code-security-review\n"
        "description: Use this skill when reviewing code security.\n---\n\n"
        "# Security review\n\n- Run focused tests.\n- Check untrusted inputs.",
        encoding="utf-8",
    )
    before = {first: first.read_bytes(), second: second.read_bytes()}
    output = tmp_path / "drafts"

    assert (
        main(
            [
                "consolidate",
                str(first),
                str(second),
                "--offline",
                "--name",
                "review-code-safely",
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    candidate = output / "review-code-safely/SKILL.md"
    assert candidate.exists()
    assert "consolidated 2 source skills" in stdout
    assert {first: first.read_bytes(), second: second.read_bytes()} == before


def test_consolidate_requires_two_distinct_skills(tmp_path, capsys):
    source = tmp_path / "skill/SKILL.md"
    source.parent.mkdir()
    source.write_text("---\nname: one\ndescription: One skill.\n---\n", encoding="utf-8")

    assert main(["consolidate", str(source)]) == 2
    assert "at least two distinct" in capsys.readouterr().err


def test_consolidate_refuses_to_overwrite_a_source_even_with_force(tmp_path, capsys):
    first = tmp_path / "skills/one/SKILL.md"
    first.parent.mkdir(parents=True)
    first.write_text(
        "---\nname: one\ndescription: Use this skill when doing one task.\n---\n\n"
        "- Keep this source.",
        encoding="utf-8",
    )
    second = tmp_path / "skills/two/SKILL.md"
    second.parent.mkdir(parents=True)
    second.write_text(
        "---\nname: two\ndescription: Use this skill when doing two tasks.\n---\n\n"
        "- Keep this source too.",
        encoding="utf-8",
    )
    original = first.read_bytes()

    assert (
        main(
            [
                "consolidate",
                str(first),
                str(second),
                "--name",
                "one",
                "--output-dir",
                str(tmp_path / "skills"),
                "--force",
            ]
        )
        == 2
    )

    assert "refusing to overwrite a source skill" in capsys.readouterr().err
    assert first.read_bytes() == original


def test_revision_refuses_to_overwrite_base_even_with_force(
    good_session,
    tmp_path,
    capsys,
):
    base = tmp_path / "skills/retry-guide/SKILL.md"
    base.parent.mkdir(parents=True)
    base.write_text(
        "---\nname: retry-guide\n"
        "description: Use this skill when changing retries.\n---\n\n"
        "# Existing\n\n1. Preserve this.",
        encoding="utf-8",
    )
    original = base.read_bytes()

    assert (
        main(
            [
                "distill",
                str(good_session),
                "--base-skill",
                str(base),
                "--skills-dir",
                str(tmp_path / "skills"),
                "--force",
            ]
        )
        == 2
    )

    assert "refusing to overwrite the base skill" in capsys.readouterr().err
    assert base.read_bytes() == original
