"""Reproducible, development-only real-repository evaluation harness.

This module is intentionally outside ``src/``. It validates skilldistill
without adding an agent runner or evaluator to the package's public API.
Raw provider and agent streams stay beneath the ignored artifact directory;
only allowlisted, sanitized records are suitable for publication.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evaluation.codex_adapter import (
    TokenUsage,
    _usage_from,
    adapt_codex_jsonl,
    sanitize_text,
)
from skilldistill.distill import distill_sessions
from skilldistill.redact import redact_text
from skilldistill.transcripts import Event, Session

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STUDY = Path(__file__).with_name("click-8.3.1.json")
DEFAULT_ARTIFACTS = ROOT / "evaluation" / "artifacts" / "pilot"
DEFAULT_RESULTS_ROOT = ROOT / "evaluation" / "results"
DEFAULT_RESULTS = DEFAULT_RESULTS_ROOT / "click-8.3.1-pilot"
DEFAULT_POST_HOLDOUT_FINDINGS = Path(__file__).with_name(
    "click-8.3.1-post-holdout-findings.json"
)
SCHEDULE_VERSION = 1
NORMALIZED_SCHEMA_VERSION = 1
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
_KEBAB_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[a-z]:[\\/]|/(?:home|Users|mnt|tmp|private|workspace|workspaces)/)"
)
_CODEX_ENV_ALLOWLIST = {
    "CODEX_HOME",
    "COLORTERM",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "USER",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
}
_INLINE_CODE = re.compile(r"`([^`\r\n]+)`")
_SHELL_FENCE = re.compile(
    r"```(?:bash|console|sh|shell)\s*\r?\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_ALLOWED_CANDIDATE_COMMANDS = {"git", "pytest", "python", "python3", "rg", "sed"}
_RECOGNIZED_CANDIDATE_COMMANDS = _ALLOWED_CANDIDATE_COMMANDS | {
    "apt",
    "bash",
    "curl",
    "make",
    "npm",
    "pip",
    "pnpm",
    "rm",
    "sh",
    "sudo",
    "tox",
    "wget",
}
_HOLDOUT_CANARIES = {
    "__heldout_nested_context__",
    "nested-context-normalization",
    "shell-token-splitting",
    "OUTER",
    "INNER",
    "LOUD",
}
_SOURCE_SPECIFICS = {
    "_split_opt(",
    "get_command(ctx",
    "original_cmd_name",
    "out.append(string)",
    "return f\"{prefix}{opt}\"",
    "src/click/parser.py",
    "src/click/shell_completion.py",
    "src/click/types.py",
    "src/click/core.py",
    "1d038f270701498433cb432f54db89f95f07a845",
}
_NESTED_CONTEXT_VERIFIER = r"""
import click
from click.testing import CliRunner

@click.group(context_settings={"token_normalize_func": str.lower})
def cli():
    pass

@cli.group()
def outer():
    pass

@outer.command()
@click.option("--name", type=click.Choice(["LOUD"], case_sensitive=False))
def inner(name):
    click.echo(name)

result = CliRunner().invoke(cli, ["OUTER", "INNER", "--NAME", "loud"])
assert result.exit_code == 0, result.output
assert result.output == "LOUD\n", result.output
"""


class EvaluationError(RuntimeError):
    """A bounded, user-safe evaluation failure."""


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


@dataclass
class ProviderCall:
    index: int
    requested_model: str
    returned_model: str | None
    duration_ms: int
    usage: TokenUsage

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "requested_model": self.requested_model,
            "returned_model": self.returned_model,
            "duration_ms": self.duration_ms,
            "usage": self.usage.to_dict(),
        }


@dataclass
class InstrumentedOpenAI:
    """Evaluation-only Responses callable that retains usage out of band."""

    api_key: str
    model: str
    artifacts: Path
    calls: list[ProviderCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - preflight reports this
            raise EvaluationError("the openai extra is not installed") from exc
        self._client = openai.OpenAI(
            api_key=self.api_key,
            timeout=60.0,
            max_retries=0,
        )

    def __call__(self, prompt: str) -> str:
        started = time.monotonic()
        try:
            response = self._client.responses.create(
                model=self.model,
                input=prompt,
                max_output_tokens=2048,
            )
        except Exception as exc:
            raise _safe_provider_error(exc) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        call = ProviderCall(
            index=len(self.calls) + 1,
            requested_model=self.model,
            returned_model=str(getattr(response, "model", "") or "") or None,
            duration_ms=duration_ms,
            usage=_usage_from(getattr(response, "usage", None)),
        )
        self.calls.append(call)
        _write_json(
            self.artifacts / f"call-{call.index}.json",
            call.to_dict(),
        )
        return str(getattr(response, "output_text", "") or "")


@dataclass
class CodexTextCallable:
    """Text-only Codex fallback used when the direct API account is blocked."""

    codex_bin: Path
    model: str
    effort: str
    timeout_seconds: int
    artifacts: Path
    calls: list[ProviderCall] = field(default_factory=list)

    def __call__(self, prompt: str) -> str:
        call_index = len(self.calls) + 1
        call_dir = self.artifacts / f"call-{call_index}"
        call_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="skilldistill-llm-") as temp:
            command = _codex_command(
                self.codex_bin,
                Path(temp),
                model=self.model,
                effort=self.effort,
                sandbox="read-only",
                skip_git=True,
            )
            result = _run_command(
                command,
                input_text=prompt,
                timeout=self.timeout_seconds,
                env=_codex_env(),
            )
        (call_dir / "raw.jsonl").write_text(result.stdout, encoding="utf-8")
        (call_dir / "raw.stderr").write_text(result.stderr, encoding="utf-8")
        trace = adapt_codex_jsonl(result.stdout, model=self.model)
        if result.returncode != 0 or result.timed_out:
            raise EvaluationError(
                "Codex text fallback failed "
                f"(exit={result.returncode}, timeout={result.timed_out})"
            )
        text = trace.session.final_assistant_text
        if not text.strip():
            raise EvaluationError("Codex text fallback returned no completed assistant text")
        call = ProviderCall(
            index=call_index,
            requested_model=self.model,
            returned_model=trace.session.model or None,
            duration_ms=result.duration_ms,
            usage=trace.usage,
        )
        self.calls.append(call)
        _write_json(call_dir / "usage.json", call.to_dict())
        return text


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)


def _run_command(
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 120,
) -> CommandResult:
    command = [str(arg) for arg in args]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    return CommandResult(
        args=command,
        returncode=process.returncode if process.returncode is not None else -9,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
    )


def _checked(
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 120,
) -> CommandResult:
    result = _run_command(args, cwd=cwd, env=env, timeout=timeout)
    if result.returncode != 0:
        command = " ".join(result.args[:4])
        raise EvaluationError(f"command failed ({result.returncode}): {command}")
    return result


def load_study(path: Path = DEFAULT_STUDY) -> dict[str, Any]:
    study = _read_json(path)
    if not isinstance(study, dict) or not isinstance(study.get("tasks"), list):
        raise EvaluationError(f"invalid study manifest: {path}")
    ids = [task.get("id") for task in study["tasks"] if isinstance(task, dict)]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise EvaluationError("study task ids must be unique and non-empty")
    roles = [task.get("role") for task in study["tasks"]]
    if roles.count("source") != 3:
        raise EvaluationError("study must define exactly three source tasks")
    if roles.count("positive_holdout") != 1 or roles.count("negative_control") != 1:
        raise EvaluationError("study must define one positive and one negative holdout")
    return study


def task_map(study: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(task["id"]): dict(task) for task in study["tasks"]}


def _ensure_child(path: Path, parent: Path) -> Path:
    resolved = path.resolve(strict=False)
    root = parent.resolve(strict=False)
    if resolved == root or root not in resolved.parents:
        raise EvaluationError(f"path escapes artifact root: {path}")
    return resolved


def _reset_generated_dir(path: Path, artifact_root: Path) -> None:
    resolved = _ensure_child(path, artifact_root)
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def _assert_artifacts_mutable(artifacts: Path) -> None:
    frozen = (artifacts / "candidate" / "freeze.json").is_file()
    candidate_started = any((artifacts / "candidate").glob("*"))
    holdouts_exist = any((artifacts / "holdouts").glob("*/result.json"))
    if frozen or candidate_started or holdouts_exist:
        raise EvaluationError(
            "artifact root contains candidate-generation or holdout results; "
            "use a fresh --artifacts directory, and use a new task split "
            "after any holdout has run"
        )


def _base_env() -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in _CODEX_ENV_ALLOWLIST
        if key in os.environ
    }
    env["TMPDIR"] = "/tmp"
    return env


def _test_env(worktree: Path) -> dict[str, str]:
    env = _base_env()
    env["PYTHONPATH"] = str(worktree / "src")
    return env


def _codex_env() -> dict[str, str]:
    return _base_env()


def _python_major_minor() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _require_python_runtime(study: Mapping[str, Any]) -> None:
    expected = str(study["runtime"]["python"])
    actual = _python_major_minor()
    if actual != expected:
        raise EvaluationError(
            f"study requires Python {expected}; current interpreter is Python {actual}"
        )


def _major_minor(version: object) -> str | None:
    match = re.match(r"^(\d+)\.(\d+)(?:\.|$)", str(version))
    return f"{match.group(1)}.{match.group(2)}" if match else None


def _cached_runtime_matches(
    marker: Mapping[str, Any],
    *,
    expected_python: str,
    expected_pytest: str,
    observed_python: str,
) -> bool:
    return (
        str(marker.get("pytest")) == expected_pytest
        and _major_minor(marker.get("python")) == expected_python
        and _major_minor(observed_python) == expected_python
    )


def _safe_env_file(path: Path) -> dict[str, str]:
    allowed = {"OPENAI_API_KEY", "SKILLDISTILL_OPENAI_MODEL"}
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise EvaluationError(f"invalid env entry at line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed:
            continue
        value = value.strip()
        if value[:1] in {"'", '"'}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise EvaluationError(f"unterminated env value at line {number}")
            value = value[1:-1]
        if "\x00" in value or "\n" in value:
            raise EvaluationError(f"invalid env value at line {number}")
        values[key] = value
    return values


def _locate_codex() -> Path:
    configured = os.environ.get("SKILLDISTILL_EVAL_CODEX_BIN")
    if configured and Path(configured).is_file():
        return Path(configured)
    discovered = shutil.which("codex")
    if discovered:
        return Path(discovered)
    candidates = sorted(
        Path("/mnt/c/Users").glob(
            "*/.vscode/extensions/openai.chatgpt-*/bin/linux-x86_64/codex"
        ),
        reverse=True,
    )
    if candidates:
        return candidates[0]
    raise EvaluationError("Codex CLI was not found")


def _codex_command(
    codex_bin: Path,
    cwd: Path,
    *,
    model: str,
    effort: str,
    sandbox: str = "workspace-write",
    skip_git: bool = False,
) -> list[str]:
    command = [
        str(codex_bin),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--color",
        "never",
        "--sandbox",
        sandbox,
        "--cd",
        str(cwd),
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{effort}"',
    ]
    if skip_git:
        command.append("--skip-git-repo-check")
    command.append("-")
    return command


def ensure_source_archive(study: Mapping[str, Any], artifacts: Path) -> Path:
    source_dir = artifacts / "_source"
    clone_dir = source_dir / "click"
    archive = source_dir / "click.tar"
    source_dir.mkdir(parents=True, exist_ok=True)
    repo = study["repository"]
    commit = str(repo["commit"])
    if not clone_dir.is_dir():
        _checked(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                str(repo["url"]),
                clone_dir,
            ],
            timeout=180,
        )
    _checked(["git", "-C", clone_dir, "fetch", "origin", commit], timeout=180)
    resolved = _checked(
        ["git", "-C", clone_dir, "rev-parse", f"{commit}^{{commit}}"]
    ).stdout.strip()
    if resolved != commit:
        raise EvaluationError(f"repository commit mismatch: expected {commit}, got {resolved}")
    if not archive.is_file():
        archive_output = archive.resolve(strict=False)
        _checked(
            [
                "git",
                "-C",
                clone_dir,
                "archive",
                "--format=tar",
                f"--output={archive_output}",
                commit,
            ],
            timeout=180,
        )
    return archive


def ensure_runtime(study: Mapping[str, Any], artifacts: Path) -> Path:
    _require_python_runtime(study)
    runtime = artifacts / "_runtime" / "venv"
    python = runtime / "bin" / "python"
    expected_pytest = str(study["runtime"]["pytest"])
    expected_python = str(study["runtime"]["python"])
    marker = runtime.parent / "versions.json"
    if python.is_file() and marker.is_file():
        saved = _read_json(marker)
        observed_python = _checked(
            [
                python,
                "-c",
                "import platform; print(platform.python_version())",
            ]
        ).stdout.strip()
        if isinstance(saved, Mapping) and _cached_runtime_matches(
            saved,
            expected_python=expected_python,
            expected_pytest=expected_pytest,
            observed_python=observed_python,
        ):
            return runtime
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    _checked([sys.executable, "-m", "venv", runtime], timeout=120)
    _checked(
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            f"pytest=={expected_pytest}",
        ],
        timeout=180,
        env=_base_env(),
    )
    _write_json(
        marker,
        {
            "python": _checked([python, "-c", "import platform; print(platform.python_version())"]).stdout.strip(),
            "pytest": expected_pytest,
        },
    )
    return runtime


def _extract_archive(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as handle:
        handle.extractall(destination, filter="data")


def prepare_task(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    run_dir: Path,
    artifacts: Path,
) -> Path:
    _reset_generated_dir(run_dir, artifacts)
    run_dir = run_dir.resolve(strict=False)
    archive = ensure_source_archive(study, artifacts)
    runtime = ensure_runtime(study, artifacts).resolve(strict=False)
    _extract_archive(archive, run_dir)
    target = _ensure_child(run_dir / str(task["path"]), run_dir)
    content = target.read_text(encoding="utf-8")
    old = str(task["old"])
    new = str(task["new"])
    if content.count(old) != 1:
        raise EvaluationError(
            f"{task['id']} mutation expected exactly one source match, got {content.count(old)}"
        )
    target.write_text(content.replace(old, new, 1), encoding="utf-8")

    _checked(["git", "init", "-q"], cwd=run_dir)
    _checked(["git", "config", "user.email", "evaluation@example.invalid"], cwd=run_dir)
    _checked(["git", "config", "user.name", "skilldistill evaluation"], cwd=run_dir)
    _checked(["git", "add", "."], cwd=run_dir)
    _checked(["git", "commit", "-q", "-m", f"seed {task['id']} regression"], cwd=run_dir)
    exclude = run_dir / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.venv\n")
    (run_dir / ".venv").symlink_to(runtime, target_is_directory=True)
    return run_dir


def _pytest_command(worktree: Path, targets: Iterable[str]) -> list[str]:
    return [
        str(worktree / ".venv" / "bin" / "python"),
        "-m",
        "pytest",
        "-q",
        *targets,
    ]


def _run_focused_verifier(
    task: Mapping[str, Any],
    worktree: Path,
    timeout: int = 180,
) -> CommandResult:
    targets = list(task["focused_verifier"])
    if targets == ["__heldout_nested_context__"]:
        return _run_command(
            [worktree / ".venv" / "bin" / "python", "-c", _NESTED_CONTEXT_VERIFIER],
            cwd=worktree,
            env=_test_env(worktree),
            timeout=timeout,
        )
    return _run_command(
        _pytest_command(worktree, targets),
        cwd=worktree,
        env=_test_env(worktree),
        timeout=timeout,
    )


def verify_repair(
    task: Mapping[str, Any],
    worktree: Path,
    *,
    timeout: int = 240,
) -> dict[str, Any]:
    focused = _run_focused_verifier(task, worktree, timeout)
    shared = _run_command(
        _pytest_command(worktree, task["shared_verifier"]),
        cwd=worktree,
        env=_test_env(worktree),
        timeout=timeout,
    )
    status = _checked(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=worktree,
    ).stdout.splitlines()
    changed = sorted(
        {
            line[3:]
            for line in status
            if len(line) > 3 and not line[3:].startswith(".venv")
        }
    )
    allowed = set(task["allowed_changes"])
    unexpected = sorted(set(changed) - allowed)
    diff = _checked(["git", "diff", "--binary"], cwd=worktree).stdout
    return {
        "passed": (
            focused.returncode == 0
            and not focused.timed_out
            and shared.returncode == 0
            and not shared.timed_out
            and not unexpected
            and bool(changed)
        ),
        "focused": {
            "returncode": focused.returncode,
            "duration_ms": focused.duration_ms,
            "timed_out": focused.timed_out,
        },
        "shared": {
            "returncode": shared.returncode,
            "duration_ms": shared.duration_ms,
            "timed_out": shared.timed_out,
        },
        "changed_files": changed,
        "unexpected_files": unexpected,
        "patch_sha256": _sha256_text(diff),
    }


def preflight(study: Mapping[str, Any], artifacts: Path) -> dict[str, Any]:
    _assert_artifacts_mutable(artifacts)
    print("preflight: preparing pinned Click archive and pytest runtime", flush=True)
    ensure_source_archive(study, artifacts)
    ensure_runtime(study, artifacts)
    task_results = []
    tasks = task_map(study)
    for task_id, task in tasks.items():
        print(f"preflight: proving seeded failure for {task_id}", flush=True)
        worktree = artifacts / "_preflight" / task_id
        worktree = prepare_task(study, task, worktree, artifacts)
        focused = _run_focused_verifier(task, worktree)
        initial_patch = _checked(["git", "show", "--format=", "--patch", "HEAD"], cwd=worktree)
        clean_literal_exposed = str(task["old"]) in initial_patch.stdout
        task_results.append(
            {
                "task_id": task_id,
                "focused_returncode": focused.returncode,
                "focused_timed_out": focused.timed_out,
                "seeded_failure_confirmed": focused.returncode != 0 and not focused.timed_out,
                "clean_literal_exposed_in_history": clean_literal_exposed,
            }
        )
    passed = all(
        row["seeded_failure_confirmed"] and not row["clean_literal_exposed_in_history"]
        for row in task_results
    )
    result = {
        "status": "passed" if passed else "failed",
        "study_sha256": _sha256_file(DEFAULT_STUDY),
        "tasks": task_results,
    }
    _write_json(artifacts / "preflight.json", result)
    if not passed:
        raise EvaluationError("one or more seeded tasks failed preflight")
    return result


def make_schedule(study: Mapping[str, Any], artifacts: Path) -> dict[str, Any]:
    _assert_artifacts_mutable(artifacts)
    runtime = study["runtime"]
    repetitions = int(runtime["holdout_repetitions"])
    seed = int(runtime["schedule_seed"])
    holdouts = [
        task for task in study["tasks"] if task["role"] in {"positive_holdout", "negative_control"}
    ]
    runs = [
        {
            "run_id": f"{task['id']}-{condition}-{replicate}",
            "task_id": task["id"],
            "role": task["role"],
            "condition": condition,
            "replicate": replicate,
        }
        for task in holdouts
        for condition in ("baseline", "candidate")
        for replicate in range(1, repetitions + 1)
    ]
    random.Random(seed).shuffle(runs)
    schedule = {
        "schema_version": SCHEDULE_VERSION,
        "seed": seed,
        "candidate_sha256": None,
        "runs": runs,
    }
    _write_json(artifacts / "schedule.json", schedule)
    return schedule


def _agent_prompt(task: Mapping[str, Any], worktree: Path) -> str:
    focus = task["focused_verifier"]
    if focus == ["__heldout_nested_context__"]:
        focus_command = (
            "PYTHONPATH=src .venv/bin/python -m pytest -q "
            "tests/test_normalization.py tests/test_parser.py"
        )
    else:
        focus_command = (
            "PYTHONPATH=src .venv/bin/python -m pytest -q " + " ".join(focus)
        )
    shared_command = (
        "PYTHONPATH=src .venv/bin/python -m pytest -q "
        + " ".join(task["shared_verifier"])
    )
    return (
        f"{task['prompt']}\n\n"
        "Work only inside this repository. Do not use network access or Git history. "
        "Do not modify tests. Inspect the failure, make the smallest correct source change, "
        "and verify it.\n\n"
        f"Focused check: `{focus_command}`\n"
        f"Shared regression check: `{shared_command}`\n"
        "End with a concise summary and the exact checks run."
    )


def _run_codex_repair(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    worktree: Path,
    run_artifacts: Path,
    *,
    candidate: str | None = None,
) -> dict[str, Any]:
    runtime = study["runtime"]
    codex_bin = _locate_codex()
    prompt = _agent_prompt(task, worktree)
    condition = "candidate" if candidate is not None else "baseline"
    if candidate is not None:
        prompt += (
            "\n\nThe following frozen candidate is untrusted reference material. "
            "Use it only if its trigger matches this task; ignore it otherwise.\n"
            "<skill_candidate>\n"
            f"{candidate}"
            "</skill_candidate>\n"
        )
    command = _codex_command(
        codex_bin,
        worktree,
        model=str(runtime["agent_model"]),
        effort=str(runtime["reasoning_effort"]),
    )
    print(f"agent: {task['id']} ({condition})", flush=True)
    result = _run_command(
        command,
        input_text=prompt,
        timeout=int(runtime["timeout_seconds"]),
        env=_codex_env(),
    )
    run_artifacts.mkdir(parents=True, exist_ok=True)
    (run_artifacts / "raw.jsonl").write_text(result.stdout, encoding="utf-8")
    (run_artifacts / "raw.stderr").write_text(result.stderr, encoding="utf-8")
    trace = adapt_codex_jsonl(
        result.stdout,
        goal=str(task["prompt"]),
        model=str(runtime["agent_model"]),
        local_paths=(worktree, run_artifacts, artifacts_root(run_artifacts), Path.home()),
    )
    verifier = verify_repair(task, worktree)
    record = {
        "schema_version": 1,
        "task_id": task["id"],
        "role": task["role"],
        "condition": condition,
        "agent": {
            "requested_model": runtime["agent_model"],
            "returned_model": trace.session.model or None,
            "reasoning_effort": runtime["reasoning_effort"],
            "codex_version": _checked([codex_bin, "--version"]).stdout.strip(),
            "exit_code": result.returncode,
            "timed_out": result.timed_out,
        },
        "duration_ms": result.duration_ms,
        "trace": trace.to_artifact(),
        "verifier": verifier,
        "verified_success": (
            result.returncode == 0
            and not result.timed_out
            and trace.successful_terminal
            and verifier["passed"]
        ),
    }
    _write_json(run_artifacts / "result.json", record)
    _write_json(run_artifacts / "normalized-trace.json", trace.to_artifact())
    return record


def artifacts_root(path: Path) -> Path:
    current = path.resolve(strict=False)
    while current.name not in {"artifacts", "results"} and current.parent != current:
        current = current.parent
    return current


def collect_sources(study: Mapping[str, Any], artifacts: Path) -> dict[str, Any]:
    _assert_artifacts_mutable(artifacts)
    source_tasks = [task for task in study["tasks"] if task["role"] == "source"]
    max_attempts = int(study["runtime"]["source_max_attempts"])
    accepted: dict[str, str] = {}
    all_attempts = []
    for task in source_tasks:
        for attempt in range(1, max_attempts + 1):
            run_dir = artifacts / "_runs" / "sources" / str(task["id"]) / str(attempt)
            result_dir = artifacts / "sources" / str(task["id"]) / str(attempt)
            run_dir = prepare_task(study, task, run_dir, artifacts)
            result = _run_codex_repair(study, task, run_dir, result_dir)
            result["attempt"] = attempt
            _write_json(result_dir / "result.json", result)
            all_attempts.append(
                {
                    "task_id": task["id"],
                    "attempt": attempt,
                    "verified_success": result["verified_success"],
                    "trace_eligible": result["trace"]["eligible"],
                }
            )
            if result["verified_success"] and result["trace"]["eligible"]:
                accepted[str(task["id"])] = str(
                    (result_dir / "normalized-trace.json").relative_to(artifacts)
                )
                break
        if str(task["id"]) not in accepted:
            break
    status = "passed" if len(accepted) == len(source_tasks) else "failed"
    summary = {"status": status, "accepted": accepted, "attempts": all_attempts}
    _write_json(artifacts / "sources" / "summary.json", summary)
    if status != "passed":
        raise EvaluationError("three verifier-backed source traces were not collected")
    return summary


def _session_from_artifact(value: Mapping[str, Any]) -> Session:
    session = Session(
        path=Path("<normalized-evaluation-trace>"),
        source=str(value.get("source", "codex")),
        goal_override=str(value.get("goal", "")),
        model=str(
            value.get("returned_model")
            or value.get("model")
            or value.get("requested_model")
            or ""
        ),
        terminal_subtype="completed" if value.get("successful_terminal") else "failed",
        terminal_is_error=not bool(value.get("successful_terminal")),
    )
    for row in value.get("events", []):
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind", ""))
        if kind not in {"user", "assistant", "tool_use", "tool_result"}:
            continue
        session.events.append(
            Event(
                kind=kind,
                text=str(row.get("text", "")),
                tool=str(row.get("tool", "")),
                is_error=bool(row.get("is_error", False)),
            )
        )
    return session


def _declared_shell_commands(content: str) -> set[str]:
    commands: set[str] = set()

    def add_snippet(snippet: str, *, force: bool) -> None:
        for segment in re.split(r"\s*(?:&&|\|\||[;|])\s*", snippet):
            tokens = segment.strip().lstrip("$").strip().split()
            while tokens and "=" in tokens[0] and not tokens[0].startswith(("./", "/")):
                tokens.pop(0)
            if not tokens:
                continue
            command = tokens[0]
            if force or len(tokens) > 1 or command in _RECOGNIZED_CANDIDATE_COMMANDS:
                commands.add(command)

    for match in _INLINE_CODE.finditer(content):
        add_snippet(match.group(1), force=False)
    for match in _SHELL_FENCE.finditer(content):
        for line in match.group(1).splitlines():
            snippet = line.strip()
            if not snippet or snippet.startswith("#"):
                continue
            add_snippet(snippet, force=True)
    return commands


def _candidate_gate(
    content: str,
    *,
    required_trigger_terms: Sequence[str] = (),
) -> dict[str, Any]:
    failures = []
    match = _FRONTMATTER.match(content)
    metadata: dict[str, Any] = {}
    if not match:
        failures.append("missing YAML frontmatter")
    else:
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            metadata = loaded
        else:
            failures.append("invalid YAML frontmatter")
    name = str(metadata.get("name", ""))
    description = " ".join(str(metadata.get("description", "")).split())
    if not _KEBAB_NAME.fullmatch(name):
        failures.append("name is not portable kebab-case")
    if not description.lower().startswith("use this skill when"):
        failures.append("description is not trigger-focused")
    if required_trigger_terms and not any(
        re.search(rf"\b{re.escape(term.lower())}\b", description.lower())
        for term in required_trigger_terms
    ):
        failures.append("description does not scope the requested trigger")
    if redact_text(content) != content:
        failures.append("candidate contains credential-like material")
    if _ABSOLUTE_PATH.search(content):
        failures.append("candidate contains an absolute machine path")
    lowered = content.lower()
    for value in sorted(_HOLDOUT_CANARIES):
        if value.lower() in lowered:
            failures.append(f"candidate contains held-out identifier: {value}")
    for value in sorted(_SOURCE_SPECIFICS):
        if value.lower() in lowered:
            failures.append(f"candidate contains source-specific literal: {value}")
    unsupported = sorted(_declared_shell_commands(content) - _ALLOWED_CANDIDATE_COMMANDS)
    if unsupported:
        failures.append(
            "candidate contains unsupported command(s): " + ", ".join(unsupported)
        )
    return {
        "passed": not failures,
        "failures": failures,
        "name": name or None,
        "description": description or None,
    }


def _safe_provider_error(exc: Exception) -> EvaluationError:
    status = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    if code is None:
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            code = body.get("code") or (
                body.get("error", {}).get("code")
                if isinstance(body.get("error"), Mapping)
                else None
            )
    if status == 429 and code == "insufficient_quota":
        return EvaluationError("provider blocked: insufficient_quota (HTTP 429)")
    if status == 429:
        return EvaluationError("provider blocked: rate limit (HTTP 429)")
    if isinstance(status, int):
        return EvaluationError(
            f"provider request failed ({type(exc).__name__}, HTTP {status})"
        )
    return EvaluationError(f"provider request failed ({type(exc).__name__})")


def provider_smoke(
    study: Mapping[str, Any],
    artifacts: Path,
    env_file: Path,
) -> dict[str, Any]:
    _assert_artifacts_mutable(artifacts)
    smoke_dir = artifacts / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    values = _safe_env_file(env_file)
    api_key = values.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = str(study["runtime"]["distillation_model"])
    if not api_key:
        result = {"status": "blocked", "reason": "OPENAI_API_KEY is not configured", "model": model}
        _write_json(smoke_dir / "status.json", result)
        return result
    previous_env = {
        key: (key in os.environ, os.environ.get(key, ""))
        for key in ("OPENAI_API_KEY", "SKILLDISTILL_OPENAI_MODEL")
    }
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["SKILLDISTILL_OPENAI_MODEL"] = model
    session = Session(
        path=Path("<provider-smoke>"),
        source="evaluation",
        goal_override="Repair a retry loop and verify the focused tests.",
        events=[
            Event(kind="tool_use", tool="shell", text="python -m pytest tests/test_retry.py"),
            Event(kind="tool_result", text="1 failed", is_error=True),
            Event(kind="tool_use", tool="apply_patch", text="repair bounded retry condition"),
            Event(kind="tool_result", text="patch applied"),
            Event(kind="tool_use", tool="shell", text="python -m pytest tests/test_retry.py"),
            Event(kind="tool_result", text="1 passed"),
            Event(kind="assistant", text="Fixed the retry condition and verified the focused test."),
        ],
    )
    try:
        llm_module = importlib.import_module("skilldistill.llm")
        llm_module = importlib.reload(llm_module)
        llm = llm_module.resolve_llm("openai")
        if llm is None:
            raise EvaluationError("OpenAI provider did not resolve")
        draft = distill_sessions([session], llm=llm, name="repair-retry-loop")
        gate = _candidate_gate(
            draft.content,
            required_trigger_terms=("retry",),
        )
        if not gate["passed"]:
            raise EvaluationError("provider smoke returned an invalid candidate")
        (smoke_dir / "SKILL.md").write_text(draft.content, encoding="utf-8")
        result = {"status": "passed", "model": model, "gate": gate}
    except (EvaluationError, RuntimeError, TypeError, ValueError) as exc:
        safe = exc if isinstance(exc, EvaluationError) else _safe_provider_error(
            exc.__cause__ if isinstance(exc.__cause__, Exception) else exc
        )
        result = {"status": "blocked", "model": model, "reason": str(safe)}
    finally:
        for key, (existed, value) in previous_env.items():
            if existed:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
    _write_json(smoke_dir / "status.json", result)
    return result


def generate_candidate(
    study: Mapping[str, Any],
    artifacts: Path,
    env_file: Path,
) -> dict[str, Any]:
    _assert_artifacts_mutable(artifacts)
    summary = _read_json(artifacts / "sources" / "summary.json")
    if summary.get("status") != "passed":
        raise EvaluationError("source collection has not passed")
    sessions = [
        _session_from_artifact(_read_json(artifacts / relative))
        for _, relative in sorted(summary["accepted"].items())
    ]
    smoke = _read_json(artifacts / "smoke" / "status.json")
    model = str(study["runtime"]["distillation_model"])
    candidate_dir = artifacts / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)

    if smoke.get("status") == "passed":
        values = _safe_env_file(env_file)
        api_key = values.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EvaluationError("provider passed smoke but API key is no longer configured")
        llm: Callable[[str], str] = InstrumentedOpenAI(
            api_key,
            model,
            candidate_dir / "provider-calls",
        )
        provider = "openai-responses"
    else:
        llm = CodexTextCallable(
            _locate_codex(),
            model,
            str(study["runtime"]["reasoning_effort"]),
            int(study["runtime"]["timeout_seconds"]),
            candidate_dir / "provider-calls",
        )
        provider = "codex-chatgpt-fallback"

    draft = distill_sessions(
        sessions,
        llm=llm,
        name="repair-cli-token-normalization",
    )
    gate = _candidate_gate(
        draft.content,
        required_trigger_terms=(
            "canonicalization",
            "canonicalize",
            "normalization",
            "normalize",
        ),
    )
    (candidate_dir / "SKILL.md").write_text(draft.content, encoding="utf-8")
    digest = _sha256_text(draft.content)
    call_records = [call.to_dict() for call in getattr(llm, "calls", [])]
    generation = {
        "status": "passed" if gate["passed"] else "failed",
        "provider": provider,
        "model": model,
        "expected_calls": 4,
        "completed_calls": len(call_records),
        "calls": call_records,
        "gate": gate,
        "candidate_sha256": digest,
    }
    _write_json(candidate_dir / "generation.json", generation)
    if not gate["passed"] or len(call_records) != 4:
        raise EvaluationError("candidate failed the predeclared static or call-count gate")
    freeze = {
        "schema_version": 1,
        "candidate_sha256": digest,
        "provider": provider,
        "model": model,
        "frozen_before_holdouts": True,
    }
    _write_json(candidate_dir / "freeze.json", freeze)
    schedule = _read_json(artifacts / "schedule.json")
    schedule["candidate_sha256"] = digest
    _write_json(artifacts / "schedule.json", schedule)
    return generation


def _frozen_holdout_context(
    study: Mapping[str, Any],
    artifacts: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    freeze_path = artifacts / "candidate" / "freeze.json"
    candidate_path = artifacts / "candidate" / "SKILL.md"
    schedule_path = artifacts / "schedule.json"
    if not freeze_path.is_file() or not candidate_path.is_file() or not schedule_path.is_file():
        raise EvaluationError("candidate, freeze record, or schedule is missing")
    freeze = _read_json(freeze_path)
    schedule = _read_json(schedule_path)
    candidate_sha256 = str(freeze.get("candidate_sha256") or "")
    if not candidate_sha256 or _sha256_file(candidate_path) != candidate_sha256:
        raise EvaluationError("frozen candidate digest mismatch")
    if schedule.get("candidate_sha256") != candidate_sha256:
        raise EvaluationError("schedule was not bound to the frozen candidate")
    binding = {
        "candidate_sha256": candidate_sha256,
        "schedule_sha256": _sha256_file(schedule_path),
        "study_config_sha256": _sha256_json(study),
    }
    return schedule, binding


def _validate_holdout_record(
    record: Mapping[str, Any],
    entry: Mapping[str, Any],
    binding: Mapping[str, str],
) -> None:
    expected: dict[str, Any] = {
        "run_id": entry["run_id"],
        "task_id": entry["task_id"],
        "role": entry["role"],
        "condition": entry["condition"],
        "replicate": entry["replicate"],
        **binding,
    }
    mismatches = [
        key for key, value in expected.items() if record.get(key) != value
    ]
    if mismatches:
        raise EvaluationError(
            f"holdout result binding mismatch for {entry['run_id']}: "
            + ", ".join(mismatches)
        )


def run_holdouts(study: Mapping[str, Any], artifacts: Path) -> list[dict[str, Any]]:
    schedule, binding = _frozen_holdout_context(study, artifacts)
    candidate_path = artifacts / "candidate" / "SKILL.md"
    candidate = candidate_path.read_text(encoding="utf-8")
    tasks = task_map(study)
    records = []
    for entry in schedule["runs"]:
        run_id = str(entry["run_id"])
        result_dir = artifacts / "holdouts" / run_id
        result_path = result_dir / "result.json"
        if result_path.is_file():
            record = _read_json(result_path)
            _validate_holdout_record(record, entry, binding)
            records.append(record)
            continue
        task = tasks[str(entry["task_id"])]
        run_dir = artifacts / "_runs" / "holdouts" / run_id
        run_dir = prepare_task(study, task, run_dir, artifacts)
        injected = candidate if entry["condition"] == "candidate" else None
        record = _run_codex_repair(
            study,
            task,
            run_dir,
            result_dir,
            candidate=injected,
        )
        record["run_id"] = run_id
        record["replicate"] = entry["replicate"]
        record.update(binding)
        _validate_holdout_record(record, entry, binding)
        _write_json(result_path, record)
        records.append(record)
    return records


def _sum_known(values: Iterable[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _median_known(values: Iterable[int | None]) -> float | None:
    known = [value for value in values if value is not None]
    return statistics.median(known) if known else None


def _post_holdout_findings(
    study: Mapping[str, Any],
    candidate_sha256: str,
) -> list[dict[str, Any]]:
    if not DEFAULT_POST_HOLDOUT_FINDINGS.is_file():
        return []
    record = _read_json(DEFAULT_POST_HOLDOUT_FINDINGS)
    if (
        record.get("study_id") != study.get("study_id")
        or record.get("candidate_sha256") != candidate_sha256
    ):
        return []
    if not record.get("recorded_after_holdouts") or record.get(
        "candidate_regenerated"
    ):
        raise EvaluationError("post-holdout finding record does not match the frozen study")
    findings = record.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(item, dict) for item in findings
    ):
        raise EvaluationError("post-holdout finding record is invalid")
    return [dict(item) for item in findings]


def aggregate(study: Mapping[str, Any], artifacts: Path) -> dict[str, Any]:
    schedule, binding = _frozen_holdout_context(study, artifacts)
    records = []
    for entry in schedule["runs"]:
        record = _read_json(
            artifacts / "holdouts" / str(entry["run_id"]) / "result.json"
        )
        _validate_holdout_record(record, entry, binding)
        records.append(record)
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for task in (item for item in study["tasks"] if "holdout" in item["role"] or item["role"] == "negative_control"):
        grouped[str(task["id"])] = {}
        for condition in ("baseline", "candidate"):
            rows = [
                record
                for record in records
                if record["task_id"] == task["id"] and record["condition"] == condition
            ]
            input_tokens = _sum_known(
                row["trace"]["usage"].get("input_tokens") for row in rows
            )
            output_tokens = _sum_known(
                row["trace"]["usage"].get("output_tokens") for row in rows
            )
            reported_tokens = (
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else None
            )
            successes = sum(bool(row["verified_success"]) for row in rows)
            grouped[str(task["id"])][condition] = {
                "runs": len(rows),
                "verified_successes": successes,
                "input_tokens": input_tokens,
                "cached_tokens": _sum_known(
                    row["trace"]["usage"].get("cached_tokens") for row in rows
                ),
                "fresh_input_tokens": _sum_known(
                    row["trace"]["usage"].get("fresh_input_tokens") for row in rows
                ),
                "output_tokens": output_tokens,
                "reasoning_tokens": _sum_known(
                    row["trace"]["usage"].get("reasoning_tokens") for row in rows
                ),
                "reported_tokens": reported_tokens,
                "reported_tokens_per_verified_success": (
                    reported_tokens / successes
                    if reported_tokens is not None and successes
                    else None
                ),
                "duration_ms": sum(int(row["duration_ms"]) for row in rows),
                "median_duration_ms": _median_known(
                    int(row["duration_ms"]) for row in rows
                ),
                "median_fresh_input_tokens": _median_known(
                    row["trace"]["usage"].get("fresh_input_tokens") for row in rows
                ),
                "median_output_tokens": _median_known(
                    row["trace"]["usage"].get("output_tokens") for row in rows
                ),
                "tool_calls": sum(int(row["trace"]["completed_tool_count"]) for row in rows),
                "turns": sum(int(row["trace"]["turn_count"]) for row in rows),
            }
    positive_id = next(
        task["id"] for task in study["tasks"] if task["role"] == "positive_holdout"
    )
    negative_id = next(
        task["id"] for task in study["tasks"] if task["role"] == "negative_control"
    )
    positive = grouped[str(positive_id)]
    negative = grouped[str(negative_id)]
    noninferior = (
        positive["candidate"]["verified_successes"]
        >= positive["baseline"]["verified_successes"]
    )
    negative_safe = (
        negative["candidate"]["verified_successes"]
        >= negative["baseline"]["verified_successes"]
    )
    improvement = (
        positive["candidate"]["verified_successes"]
        >= 2
        and positive["baseline"]["verified_successes"]
        <= 1
    )
    baseline_per_success = positive["baseline"]["reported_tokens_per_verified_success"]
    candidate_per_success = positive["candidate"][
        "reported_tokens_per_verified_success"
    ]
    token_threshold = (
        baseline_per_success is not None
        and candidate_per_success is not None
        and candidate_per_success <= baseline_per_success * 0.9
    )
    generation = _read_json(artifacts / "candidate" / "generation.json")
    generation_calls = generation.get("calls", [])
    generation_usage = {
        "calls": len(generation_calls),
        "input_tokens": _sum_known(
            call.get("usage", {}).get("input_tokens") for call in generation_calls
        ),
        "cached_tokens": _sum_known(
            call.get("usage", {}).get("cached_tokens") for call in generation_calls
        ),
        "fresh_input_tokens": _sum_known(
            call.get("usage", {}).get("fresh_input_tokens")
            for call in generation_calls
        ),
        "output_tokens": _sum_known(
            call.get("usage", {}).get("output_tokens") for call in generation_calls
        ),
        "duration_ms": sum(
            int(call.get("duration_ms", 0)) for call in generation_calls
        ),
    }
    candidate_sha256 = str(schedule["candidate_sha256"])
    post_holdout_findings = _post_holdout_findings(study, candidate_sha256)
    trigger_specificity_established = not any(
        item.get("id") == "candidate-trigger-scope-too-broad"
        for item in post_holdout_findings
    )
    metrics = {
        "schema_version": 1,
        "candidate_sha256": candidate_sha256,
        "candidate_generation": generation_usage,
        "tasks": grouped,
        "validity": {
            "negative_control_trigger_specificity_established": (
                trigger_specificity_established
            ),
            "post_holdout_findings": post_holdout_findings,
        },
        "decision": {
            "success_noninferior": noninferior,
            "negative_control_no_regression": negative_safe,
            "net_new_task_success": improvement,
            "evidence_of_improvement": noninferior and negative_safe and improvement,
            "reported_token_reduction_at_least_10_percent": token_threshold,
            "efficiency_claim_supported": (
                noninferior and negative_safe and token_threshold
            ),
            "universal_claim_supported": False,
        },
    }
    _write_json(artifacts / "metrics.json", metrics)
    _write_report(study, artifacts, metrics)
    return metrics


def _write_report(
    study: Mapping[str, Any],
    artifacts: Path,
    metrics: Mapping[str, Any],
) -> None:
    source = _read_json(artifacts / "sources" / "summary.json")
    smoke = _read_json(artifacts / "smoke" / "status.json")
    generation = _read_json(artifacts / "candidate" / "generation.json")
    lines = [
        "# Click 8.3.1 evaluation result",
        "",
        f"- Study: `{study['study_id']}`",
        f"- Provider smoke: **{smoke['status']}**",
        f"- Source collection: **{source['status']}**",
        f"- Candidate generation: **{generation['status']}** via `{generation['provider']}`",
        f"- Candidate SHA-256: `{metrics['candidate_sha256']}`",
        (
            "- One-time candidate generation: "
            f"{metrics['candidate_generation']['calls']} calls, "
            f"{metrics['candidate_generation']['input_tokens']} input tokens, "
            f"{metrics['candidate_generation']['output_tokens']} output tokens"
        ),
        "",
        "## Held-out results",
        "",
        (
            "| Task | Condition | Verified | Runs | Input | Cached | Fresh input | "
            "Output | Latency ms | Turns | Tool calls |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for task_id, conditions in metrics["tasks"].items():
        for condition, row in conditions.items():
            lines.append(
                f"| `{task_id}` | {condition} | {row['verified_successes']} | "
                f"{row['runs']} | {row['input_tokens']} | {row['cached_tokens']} | "
                f"{row['fresh_input_tokens']} | {row['output_tokens']} | "
                f"{row['duration_ms']} | {row['turns']} | {row['tool_calls']} |"
            )
    decision = metrics["decision"]
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Success non-inferior: **{decision['success_noninferior']}**",
            f"- Negative control without regression: **{decision['negative_control_no_regression']}**",
            f"- Net new held-out success: **{decision['net_new_task_success']}**",
            (
                "- Reported-token reduction of at least 10%: "
                f"**{decision['reported_token_reduction_at_least_10_percent']}**"
            ),
            f"- Evidence of improvement on this task set: **{decision['evidence_of_improvement']}**",
            f"- Efficiency claim supported: **{decision['efficiency_claim_supported']}**",
            "",
            "This controlled pilot does not support a universal performance or savings claim.",
            "",
        ]
    )
    findings = metrics.get("validity", {}).get("post_holdout_findings", [])
    if findings:
        lines.extend(
            [
                "## Post-holdout protocol finding",
                "",
                *[
                    f"- **{item['summary']}** {item['impact']}"
                    for item in findings
                ],
                "",
                (
                    "The frozen candidate was not changed or regenerated. "
                    "Addressing this finding requires a new task split."
                ),
                "",
            ]
        )
    (artifacts / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def _sanitize_json_value(value: Any, local_paths: Sequence[Path]) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, local_paths=local_paths)
    if isinstance(value, list):
        return [_sanitize_json_value(item, local_paths) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_json_value(item, local_paths)
            for key, item in value.items()
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(str(value), local_paths=local_paths)


def _normalize_codex_model_metadata(value: Any) -> Any:
    if isinstance(value, list):
        for item in value:
            _normalize_codex_model_metadata(item)
        return value
    if not isinstance(value, dict):
        return value
    for item in value.values():
        _normalize_codex_model_metadata(item)
    if value.get("source") == "codex" and "successful_terminal" in value:
        if "requested_model" not in value:
            value["requested_model"] = value.get("model")
            value["returned_model"] = None
        value["model"] = value.get("returned_model")
    if value.get("provider") == "codex-chatgpt-fallback":
        for call in value.get("calls", []):
            if isinstance(call, dict):
                call["returned_model"] = None
    trace = value.get("trace")
    agent = value.get("agent")
    if isinstance(trace, dict) and isinstance(agent, dict):
        agent["returned_model"] = trace.get("returned_model")
    return value


def _copy_sanitized_json(source: Path, target: Path, local_paths: Sequence[Path]) -> None:
    value = _normalize_codex_model_metadata(_read_json(source))
    _write_json(target, _sanitize_json_value(value, local_paths))


def _copy_holdout_summary(source: Path, target: Path, local_paths: Sequence[Path]) -> None:
    value = _normalize_codex_model_metadata(_read_json(source))
    trace = value.get("trace")
    if isinstance(trace, dict):
        trace.pop("events", None)
    _write_json(target, _sanitize_json_value(value, local_paths))


def _source_state_sha256() -> str:
    paths = _checked(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    ).stdout.split("\0")
    digest = hashlib.sha256()
    for relative in sorted(item for item in paths if item):
        if relative.startswith(("evaluation/artifacts/", "evaluation/results/")):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_publish_destination(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    results_root = DEFAULT_RESULTS_ROOT.resolve(strict=False)
    if resolved == results_root or results_root not in resolved.parents:
        raise EvaluationError(
            f"publication destination must be a child of {DEFAULT_RESULTS_ROOT}"
        )
    return resolved


def publish_results(artifacts: Path, destination: Path = DEFAULT_RESULTS) -> Path:
    destination = _safe_publish_destination(destination)
    if not (artifacts / "metrics.json").is_file():
        raise EvaluationError("aggregate metrics do not exist")
    aggregate(load_study(), artifacts)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    allowlisted = [
        Path("manifest.json"),
        Path("preflight.json"),
        Path("schedule.json"),
        Path("smoke/status.json"),
        Path("smoke/SKILL.md"),
        Path("sources/summary.json"),
        Path("candidate/SKILL.md"),
        Path("candidate/generation.json"),
        Path("candidate/freeze.json"),
        Path("metrics.json"),
        Path("REPORT.md"),
    ]
    for relative in allowlisted:
        source = artifacts / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".json":
            _copy_sanitized_json(source, target, (artifacts, Path.home()))
        else:
            target.write_text(
                sanitize_text(source.read_text(encoding="utf-8"), local_paths=(artifacts, Path.home())),
                encoding="utf-8",
            )
    for path in sorted((artifacts / "sources").glob("*/*/normalized-trace.json")):
        target = destination / path.relative_to(artifacts)
        _copy_sanitized_json(path, target, (artifacts, Path.home()))
    for path in sorted((artifacts / "holdouts").glob("*/result.json")):
        target = destination / path.relative_to(artifacts)
        _copy_holdout_summary(path, target, (artifacts, Path.home()))
    if DEFAULT_POST_HOLDOUT_FINDINGS.is_file():
        _copy_sanitized_json(
            DEFAULT_POST_HOLDOUT_FINDINGS,
            destination / "post-holdout-findings.json",
            (artifacts, Path.home()),
        )
    provenance = {
        "schema_version": 1,
        "study_manifest_sha256": _sha256_file(DEFAULT_STUDY),
        "source_state_sha256": _source_state_sha256(),
        "skilldistill_commit": _checked(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip(),
        "skilldistill_dirty": bool(
            _checked(["git", "status", "--porcelain"], cwd=ROOT).stdout.strip()
        ),
        "published_files": sorted(
            {
                str(path.relative_to(destination))
                for path in destination.rglob("*")
                if path.is_file()
            }
            | {"provenance.json"}
        ),
    }
    _write_json(destination / "provenance.json", provenance)
    return destination


def write_run_manifest(study: Mapping[str, Any], artifacts: Path) -> None:
    codex = _locate_codex()
    value = {
        "schema_version": 1,
        "study_id": study["study_id"],
        "repository": study["repository"],
        "runtime": study["runtime"],
        "study_manifest_sha256": _sha256_file(DEFAULT_STUDY),
        "codex_version": _checked([codex, "--version"]).stdout.strip(),
        "python_version": sys.version.split()[0],
        "raw_artifacts_publishable": False,
    }
    path = artifacts / "manifest.json"
    if path.is_file():
        if _read_json(path) != value:
            raise EvaluationError(
                "run environment differs from the existing artifact manifest; "
                "use a fresh --artifacts directory"
            )
        return
    _write_json(path, value)


def run_pipeline(
    study: Mapping[str, Any],
    artifacts: Path,
    env_file: Path,
    *,
    stage: str,
) -> None:
    _require_python_runtime(study)
    if stage in {"all", "preflight", "plan", "smoke", "sources", "distill"}:
        _assert_artifacts_mutable(artifacts)
    if stage == "report":
        if not (artifacts / "manifest.json").is_file():
            raise EvaluationError("run manifest does not exist")
    else:
        artifacts.mkdir(parents=True, exist_ok=True)
        write_run_manifest(study, artifacts)
    if stage in {"all", "preflight"}:
        preflight(study, artifacts)
        if stage == "preflight":
            return
    if not (artifacts / "preflight.json").is_file():
        raise EvaluationError("run preflight before paid stages")
    if stage in {"all", "plan"}:
        make_schedule(study, artifacts)
        if stage == "plan":
            return
    if not (artifacts / "schedule.json").is_file():
        make_schedule(study, artifacts)
    if stage in {"all", "smoke"}:
        smoke = provider_smoke(study, artifacts, env_file)
        print(f"provider smoke: {smoke['status']}", flush=True)
        if stage == "smoke":
            return
    if not (artifacts / "smoke" / "status.json").is_file():
        raise EvaluationError("run provider smoke before source collection")
    if stage in {"all", "sources"}:
        collect_sources(study, artifacts)
        if stage == "sources":
            return
    if stage in {"all", "distill"}:
        generate_candidate(study, artifacts, env_file)
        if stage == "distill":
            return
    if stage in {"all", "holdouts"}:
        run_holdouts(study, artifacts)
        aggregate(study, artifacts)
        if stage == "holdouts":
            return
    if stage in {"all", "report"}:
        metrics = aggregate(study, artifacts)
        print(json.dumps(metrics["decision"], indent=2), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "plan", "smoke", "sources", "distill", "holdouts", "report"):
        command = subparsers.add_parser(name)
        command.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
        command.add_argument("--env-file", type=Path, default=ROOT / ".env")
    run = subparsers.add_parser("run")
    run.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    run.add_argument("--env-file", type=Path, default=ROOT / ".env")
    run.add_argument("--execute", action="store_true")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    publish.add_argument("--destination", type=Path, default=DEFAULT_RESULTS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            destination = publish_results(args.artifacts, args.destination)
            print(f"published sanitized results to {destination}")
            return 0
        study = load_study(args.study)
        if args.command == "run":
            if not args.execute:
                parser.error("run requires --execute because it performs paid model calls")
            stage = "all"
        else:
            stage = args.command
        run_pipeline(study, args.artifacts, args.env_file, stage=stage)
        return 0
    except EvaluationError as exc:
        print(f"evaluation: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
