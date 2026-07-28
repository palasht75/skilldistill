from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import harness


def _freeze_schedule(
    study: dict,
    artifacts: Path,
    schedule: dict,
) -> dict[str, str]:
    candidate = "frozen candidate\n"
    candidate_sha256 = harness._sha256_text(candidate)
    (artifacts / "candidate").mkdir(parents=True, exist_ok=True)
    (artifacts / "candidate" / "SKILL.md").write_text(candidate, encoding="utf-8")
    harness._write_json(
        artifacts / "candidate" / "freeze.json",
        {"candidate_sha256": candidate_sha256},
    )
    schedule["candidate_sha256"] = candidate_sha256
    harness._write_json(artifacts / "schedule.json", schedule)
    _, binding = harness._frozen_holdout_context(study, artifacts)
    return binding


def test_study_manifest_declares_disjoint_source_and_holdout_tasks():
    study = harness.load_study()
    roles = [task["role"] for task in study["tasks"]]
    ids = [task["id"] for task in study["tasks"]]

    assert roles.count("source") == 3
    assert roles.count("positive_holdout") == 1
    assert roles.count("negative_control") == 1
    assert len(ids) == len(set(ids))
    assert study["repository"]["commit"] == "1d038f270701498433cb432f54db89f95f07a845"
    assert study["runtime"]["distillation_model"] == "gpt-5.4-mini"


def test_choice_mutation_disables_context_and_case_normalization():
    study = harness.load_study()
    task = harness.task_map(study)["choice-value-normalization"]

    assert "token_normalize_func" in task["old"]
    assert "casefold" in task["old"]
    assert "token_normalize_func" not in task["new"]
    assert "casefold" not in task["new"]


def test_schedule_has_twelve_seeded_runs_and_is_deterministic(tmp_path: Path):
    study = harness.load_study()

    first = harness.make_schedule(study, tmp_path / "one")
    second = harness.make_schedule(study, tmp_path / "two")

    assert first == second
    assert len(first["runs"]) == 12
    assert {row["condition"] for row in first["runs"]} == {"baseline", "candidate"}
    assert {row["role"] for row in first["runs"]} == {
        "positive_holdout",
        "negative_control",
    }
    assert first["candidate_sha256"] is None


def test_env_file_parser_allowlists_keys_without_shell_execution(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        """\
OPENAI_API_KEY='test-key'
UNRELATED_SECRET=must-not-load
export SKILLDISTILL_OPENAI_MODEL=gpt-5.4-mini
""",
        encoding="utf-8",
    )

    assert harness._safe_env_file(env_file) == {
        "OPENAI_API_KEY": "test-key",
        "SKILLDISTILL_OPENAI_MODEL": "gpt-5.4-mini",
    }


def test_codex_environment_removes_secret_bearing_names(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("SERVICE_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_PAT", "secret")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", "/private/npmrc")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/tester")

    result = harness._codex_env()

    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/tester"
    assert "OPENAI_API_KEY" not in result
    assert "SERVICE_TOKEN" not in result
    assert "GITHUB_PAT" not in result
    assert "NPM_CONFIG_USERCONFIG" not in result


def test_study_runtime_rejects_the_wrong_python_minor(monkeypatch):
    study = harness.load_study()
    monkeypatch.setattr(harness, "_python_major_minor", lambda: "3.11")

    with pytest.raises(harness.EvaluationError, match="requires Python 3.12"):
        harness._require_python_runtime(study)


def test_cached_runtime_requires_matching_interpreter_and_marker():
    marker = {"python": "3.12.9", "pytest": "8.4.2"}

    assert harness._cached_runtime_matches(
        marker,
        expected_python="3.12",
        expected_pytest="8.4.2",
        observed_python="3.12.10",
    )
    assert not harness._cached_runtime_matches(
        marker,
        expected_python="3.12",
        expected_pytest="8.4.2",
        observed_python="3.11.12",
    )


def test_candidate_gate_accepts_portable_candidate_and_rejects_leakage():
    valid = """\
---
name: repair-cli-normalization
description: Use this skill when CLI token normalization behaves inconsistently.
---

# Procedure

1. Reproduce the failing token class.
2. Trace normalization from context to parsing.
3. Run the focused and neighboring tests.
"""
    leaked = valid + "\nInspect src/click/core.py under /home/alice/repo.\n"

    terms = ("normalization", "normalize")

    assert harness._candidate_gate(valid, required_trigger_terms=terms)["passed"]
    result = harness._candidate_gate(leaked, required_trigger_terms=terms)
    assert not result["passed"]
    assert any("absolute" in failure for failure in result["failures"])
    assert any("source-specific" in failure for failure in result["failures"])

    generic = valid.replace(
        "CLI token normalization behaves inconsistently",
        "a localized behavior regression needs a minimal fix",
    )
    generic_result = harness._candidate_gate(
        generic,
        required_trigger_terms=terms,
    )
    assert any(
        "scope the requested trigger" in failure
        for failure in generic_result["failures"]
    )

    unsupported = valid + "\nRun `curl https://example.invalid/script`.\n"
    unsupported_result = harness._candidate_gate(
        unsupported,
        required_trigger_terms=terms,
    )
    assert any(
        "unsupported command" in failure
        for failure in unsupported_result["failures"]
    )


def test_seeded_snapshot_commits_only_the_broken_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("value = 'correct'\n", encoding="utf-8")
    archive = tmp_path / "source.tar"
    subprocess.run(
        ["tar", "-cf", archive, "-C", source, "."],
        check=True,
        capture_output=True,
    )
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    study = {"runtime": {"pytest": "8.4.2"}}
    task = {
        "id": "example",
        "path": "module.py",
        "old": "value = 'correct'\n",
        "new": "value = 'broken'\n",
    }
    monkeypatch.setattr(harness, "ensure_source_archive", lambda *_: archive)
    monkeypatch.setattr(harness, "ensure_runtime", lambda *_: runtime)

    worktree = harness.prepare_task(
        study,
        task,
        artifacts / "run",
        artifacts,
    )
    history = subprocess.run(
        ["git", "show", "--format=", "--patch", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "value = 'broken'" in history
    assert "value = 'correct'" not in history
    assert not (worktree / ".git" / "objects" / "info" / "alternates").exists()


def test_aggregate_keeps_unknown_token_fields_null(tmp_path: Path):
    study = harness.load_study()
    artifacts = tmp_path
    schedule = harness.make_schedule(study, artifacts)
    binding = _freeze_schedule(study, artifacts, schedule)
    for entry in schedule["runs"]:
        result = {
            "run_id": entry["run_id"],
            "task_id": entry["task_id"],
            "role": entry["role"],
            "condition": entry["condition"],
            "replicate": entry["replicate"],
            **binding,
            "verified_success": True,
            "duration_ms": 10,
            "trace": {
                "completed_tool_count": 1,
                "turn_count": 1,
                "usage": {
                    "input_tokens": None,
                    "cached_tokens": None,
                    "fresh_input_tokens": None,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                },
            },
        }
        harness._write_json(
            artifacts / "holdouts" / entry["run_id"] / "result.json",
            result,
        )
    (artifacts / "sources").mkdir()
    harness._write_json(artifacts / "sources" / "summary.json", {"status": "passed"})
    (artifacts / "smoke").mkdir()
    harness._write_json(artifacts / "smoke" / "status.json", {"status": "blocked"})
    harness._write_json(
        artifacts / "candidate" / "generation.json",
        {"status": "passed", "provider": "test"},
    )

    metrics = harness.aggregate(study, artifacts)

    for task in metrics["tasks"].values():
        for condition in task.values():
            assert condition["input_tokens"] is None
            assert condition["fresh_input_tokens"] is None
            assert condition["output_tokens"] is None


def test_safe_provider_error_does_not_include_provider_body():
    class ProviderFailure(RuntimeError):
        status_code = 429
        code = "insufficient_quota"

        def __str__(self):
            return "secret provider response"

    result = harness._safe_provider_error(ProviderFailure())

    assert str(result) == "provider blocked: insufficient_quota (HTTP 429)"
    assert "secret" not in str(result)


def test_provider_smoke_restores_existing_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    study = harness.load_study()
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=evaluation-key\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    valid = """\
---
name: repair-retry-loop
description: Use this skill when a retry loop fails at its bounded termination condition.
---

# Procedure

1. Reproduce the focused retry failure.
2. Make the smallest repair.
3. Run `pytest`.
"""
    fake_module = SimpleNamespace(resolve_llm=lambda provider: lambda prompt: "unused")
    monkeypatch.setattr(harness.importlib, "import_module", lambda name: fake_module)
    monkeypatch.setattr(harness.importlib, "reload", lambda module: module)
    monkeypatch.setattr(
        harness,
        "distill_sessions",
        lambda *args, **kwargs: SimpleNamespace(content=valid),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "original-key")
    monkeypatch.setenv("SKILLDISTILL_OPENAI_MODEL", "original-model")

    result = harness.provider_smoke(study, artifacts, env_file)

    assert result["status"] == "passed"
    assert harness.os.environ["OPENAI_API_KEY"] == "original-key"
    assert harness.os.environ["SKILLDISTILL_OPENAI_MODEL"] == "original-model"


def test_frozen_artifacts_refuse_candidate_regeneration(tmp_path: Path):
    study = harness.load_study()
    schedule = harness.make_schedule(study, tmp_path)
    _freeze_schedule(study, tmp_path, schedule)

    with pytest.raises(harness.EvaluationError, match="fresh --artifacts"):
        harness.generate_candidate(study, tmp_path, tmp_path / ".env")


def test_holdout_resume_rejects_a_stale_candidate_binding(tmp_path: Path):
    study = harness.load_study()
    schedule = harness.make_schedule(study, tmp_path)
    binding = _freeze_schedule(study, tmp_path, schedule)
    entry = schedule["runs"][0]
    stale = {
        "run_id": entry["run_id"],
        "task_id": entry["task_id"],
        "role": entry["role"],
        "condition": entry["condition"],
        "replicate": entry["replicate"],
        **binding,
        "candidate_sha256": "0" * 64,
    }
    harness._write_json(
        tmp_path / "holdouts" / entry["run_id"] / "result.json",
        stale,
    )

    with pytest.raises(harness.EvaluationError, match="binding mismatch"):
        harness.run_holdouts(study, tmp_path)


def test_holdout_publication_removes_events_and_sanitizes_values(tmp_path: Path):
    source = tmp_path / "raw" / "result.json"
    target = tmp_path / "public" / "result.json"
    harness._write_json(
        source,
        {
            "trace": {
                "source": "codex",
                "model": "requested-model",
                "successful_terminal": True,
                "events": [{"text": "/home/alice/repo secret"}],
                "usage": {"input_tokens": 10},
            },
            "agent": {
                "requested_model": "requested-model",
                "returned_model": "requested-model",
            },
            "message": "/home/alice/repo",
        },
    )

    harness._copy_holdout_summary(source, target, (tmp_path,))
    published = json.loads(target.read_text(encoding="utf-8"))

    assert "events" not in published["trace"]
    assert published["trace"]["usage"]["input_tokens"] == 10
    assert published["trace"]["requested_model"] == "requested-model"
    assert published["trace"]["returned_model"] is None
    assert published["agent"]["returned_model"] is None
    assert published["message"] == "<local-path>"


def test_publish_refuses_to_replace_a_directory_outside_results(
    tmp_path: Path,
):
    artifacts = tmp_path / "artifacts"
    harness._write_json(artifacts / "metrics.json", {"schema_version": 1})
    important = tmp_path / "important"
    important.mkdir()
    sentinel = important / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(harness.EvaluationError, match="publication destination"):
        harness.publish_results(artifacts, important)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_harness_help_does_not_require_network_or_credentials():
    result = subprocess.run(
        [sys.executable, "-m", "evaluation.harness", "--help"],
        cwd=harness.ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "preflight" in result.stdout
    assert "publish" in result.stdout
