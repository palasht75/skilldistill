"""skilldistill CLI: scan sessions, distill skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skilldistill import __version__
from skilldistill.consolidate import consolidate_skills
from skilldistill.dedup import find_overlaps, find_similar
from skilldistill.distill import distill_sessions
from skilldistill.emitter import (
    InvalidSkillNameError,
    SkillExistsError,
    UnsafeSkillPathError,
    write_skill,
)
from skilldistill.llm import LLMProviderError, resolve_llm
from skilldistill.transcripts import find_sessions, parse_session

DEFAULT_SESSIONS = "~/.claude/projects"
DEFAULT_DRAFTS = "skill-drafts"


def _read_bounded(path: Path, max_chars: int = 30_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(max_chars + 1)
    except OSError as exc:
        raise ValueError(f"could not read {path}") from exc
    if len(content) > max_chars:
        raise ValueError(f"{path} exceeds the {max_chars:,}-character safety limit")
    return content


def _provider(args):
    provider = "offline" if args.offline else args.provider
    llm = resolve_llm(provider=provider)
    if llm is None and provider != "offline":
        raise ValueError(
            f"{provider} provider is not configured "
            "(set its API key and install the matching extra)"
        )
    return provider, llm


def _source_collision(
    output_dir: Path | str,
    draft_name: str,
    source_paths: list[Path | str],
) -> Path | None:
    candidate = (Path(output_dir).expanduser() / draft_name / "SKILL.md").resolve(
        strict=False
    )
    for source in source_paths:
        source_path = Path(source).expanduser()
        try:
            resolved = source_path.resolve(strict=True)
        except OSError:
            continue
        if resolved == candidate:
            return source_path
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="skilldistill",
        description="Turn selected agent trajectories into reviewable skill candidates.",
    )
    parser.add_argument("--version", action="version", version=f"skilldistill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="rank sessions worth distilling")
    p_scan.add_argument(
        "root",
        nargs="?",
        default=None,
        help=f"sessions dir (Claude default: {DEFAULT_SESSIONS}; Cursor default: .)",
    )
    p_scan.add_argument(
        "--source",
        choices=("auto", "claude", "cursor"),
        default="auto",
        help=(
            "discovery hint; Cursor adds exported Markdown, while compatible "
            "JSONL is always considered"
        ),
    )
    p_scan.add_argument("--min-score", type=float, default=0.5)
    p_scan.add_argument("--limit", type=int, default=20, help="max sessions to inspect")
    p_scan.add_argument("--json", action="store_true")

    p_overlaps = sub.add_parser(
        "overlaps",
        help="rank lexical overlap candidates in an existing skill library",
    )
    p_overlaps.add_argument("root", help="skill library containing **/SKILL.md")
    p_overlaps.add_argument("--threshold", type=float, default=0.35)
    p_overlaps.add_argument("--limit", type=int, default=50)
    p_overlaps.add_argument("--max-skills", type=int, default=500)
    p_overlaps.add_argument("--json", action="store_true")

    p_dist = sub.add_parser(
        "distill",
        help="distill one or more related sessions into a SKILL.md draft",
    )
    p_dist.add_argument(
        "session",
        nargs="+",
        help="Claude/Cursor JSONL or Cursor Markdown; pass several related traces to synthesize",
    )
    p_dist.add_argument(
        "--goal",
        action="append",
        help=(
            "goal override for a stream that omits its prompt; repeat once per "
            "session, in the same order"
        ),
    )
    p_dist.add_argument("--name", help="force the candidate skill name")
    p_dist.add_argument(
        "--base-skill",
        help=(
            "frozen SKILL.md context for a replacement candidate; "
            "the source file is never modified"
        ),
    )
    p_dist.add_argument(
        "--output-dir",
        "--skills-dir",
        dest="output_dir",
        default=DEFAULT_DRAFTS,
        help=(
            f"review directory for candidates (default: {DEFAULT_DRAFTS}); "
            "--skills-dir is a compatibility alias"
        ),
    )
    p_dist.add_argument(
        "--compare-dir",
        action="append",
        default=[],
        help="existing skill library to check for similar candidates; repeatable",
    )
    dist_provider = p_dist.add_mutually_exclusive_group()
    dist_provider.add_argument(
        "--offline",
        action="store_true",
        help="skip LLM, emit outline draft",
    )
    dist_provider.add_argument(
        "--provider",
        choices=("offline", "openai", "anthropic", "auto"),
        default="offline",
        help="cloud provider is opt-in; transcript excerpts are redacted before egress",
    )
    p_dist.add_argument("--force", action="store_true", help="overwrite an existing skill")

    p_consolidate = sub.add_parser(
        "consolidate",
        help="propose one draft from two or more overlapping existing skills",
    )
    p_consolidate.add_argument(
        "skill",
        nargs="+",
        help="paths to existing SKILL.md files; sources are never modified",
    )
    p_consolidate.add_argument("--name", help="force the candidate skill name")
    p_consolidate.add_argument(
        "--output-dir",
        default="skill-drafts",
        help="review directory for the candidate (default: skill-drafts)",
    )
    consolidate_provider = p_consolidate.add_mutually_exclusive_group()
    consolidate_provider.add_argument(
        "--offline",
        action="store_true",
        help="skip LLM and emit an exact-line consolidation scaffold",
    )
    consolidate_provider.add_argument(
        "--provider",
        choices=("offline", "openai", "anthropic", "auto"),
        default="offline",
        help="cloud provider is opt-in; skill contents are redacted before egress",
    )
    p_consolidate.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "overlaps":
        try:
            overlaps = find_overlaps(
                args.root,
                threshold=args.threshold,
                limit=args.limit,
                max_skills=args.max_skills,
            )
        except ValueError as exc:
            print(f"skilldistill: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "left": str(overlap.left_path),
                            "left_name": overlap.left_name,
                            "right": str(overlap.right_path),
                            "right_name": overlap.right_name,
                            "similarity": overlap.similarity,
                            "shared_terms": overlap.shared_terms,
                        }
                        for overlap in overlaps
                    ],
                    indent=2,
                )
            )
        elif not overlaps:
            print("no lexical overlap candidates above threshold")
        else:
            for overlap in overlaps:
                print(
                    f"[{overlap.similarity:.2f}] "
                    f"{overlap.left_name} <> {overlap.right_name}"
                )
                print(f"       shared: {', '.join(overlap.shared_terms)}")
                print(f"       files:  {overlap.left_path} | {overlap.right_path}")
        return 0

    if args.command == "scan":
        from skilldistill.detect import score_session

        if not 0 <= args.min_score <= 1:
            print("skilldistill: --min-score must be between 0 and 1", file=sys.stderr)
            return 2
        if args.limit < 1:
            print("skilldistill: --limit must be positive", file=sys.stderr)
            return 2
        default_root = "." if args.source == "cursor" else DEFAULT_SESSIONS
        root = Path(args.root or default_root).expanduser()
        if not root.is_dir():
            print(f"skilldistill: sessions dir not found: {root}", file=sys.stderr)
            return 2
        rows = []
        for path in find_sessions(root, source=args.source)[: args.limit]:
            session = parse_session(path)
            sc = score_session(session)
            if sc.score >= args.min_score:
                rows.append(
                    {
                        "path": str(path),
                        "score": sc.score,
                        "goal": session.first_goal[:100],
                        "reasons": sc.reasons,
                    }
                )
        rows.sort(key=lambda r: -r["score"])
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            if not rows:
                print("no sessions above threshold — try --min-score 0.3 or check the path")
            for r in rows:
                print(f"[{r['score']:.2f}] {r['path']}")
                print(f"       goal: {r['goal']}")
                print(f"       why:  {'; '.join(r['reasons'])}")
        return 0

    if args.command == "consolidate":
        try:
            _, llm = _provider(args)
            draft = consolidate_skills(args.skill, llm=llm, name=args.name)
            collision = _source_collision(args.output_dir, draft.name, args.skill)
            if collision:
                print(
                    "skilldistill: refusing to overwrite a source skill during "
                    f"consolidation: {collision}",
                    file=sys.stderr,
                )
                return 2
            target = write_skill(draft, args.output_dir, force=args.force)
        except (TypeError, ValueError) as exc:
            print(f"skilldistill: {exc}", file=sys.stderr)
            return 2
        except LLMProviderError as exc:
            print(f"skilldistill: {exc}", file=sys.stderr)
            return 1
        except (InvalidSkillNameError, SkillExistsError, UnsafeSkillPathError) as exc:
            print(f"skilldistill: {exc}", file=sys.stderr)
            return 1
        print(
            f"wrote {target}  (origin: {draft.origin}; "
            f"consolidated {draft.source_count} source skills)"
        )
        print("source skills were not modified; evaluate the candidate before promotion")
        return 0

    # distill one or more sessions
    session_paths = [Path(path).expanduser() for path in args.session]
    missing = next((path for path in session_paths if not path.is_file()), None)
    if missing:
        print(f"skilldistill: session not found: {missing}", file=sys.stderr)
        return 2
    if args.goal and len(args.goal) != len(session_paths):
        print(
            "skilldistill: repeat --goal exactly once per session, in the same order",
            file=sys.stderr,
        )
        return 2
    invalid_compare_dir = next(
        (
            Path(path).expanduser()
            for path in args.compare_dir
            if not Path(path).expanduser().is_dir()
        ),
        None,
    )
    if invalid_compare_dir:
        print(
            f"skilldistill: comparison directory not found: {invalid_compare_dir}",
            file=sys.stderr,
        )
        return 2
    sessions = [parse_session(path) for path in session_paths]
    if args.goal:
        for session, goal in zip(sessions, args.goal):
            session.goal_override = goal
    try:
        _, llm = _provider(args)
        base_skill = None
        if args.base_skill:
            base_path = Path(args.base_skill).expanduser()
            if not base_path.is_file():
                print(f"skilldistill: base skill not found: {base_path}", file=sys.stderr)
                return 2
            base_skill = _read_bounded(base_path)
        draft = distill_sessions(
            sessions,
            llm=llm,
            name=args.name,
            base_skill=base_skill,
        )
    except (TypeError, ValueError) as exc:
        print(f"skilldistill: {exc}", file=sys.stderr)
        return 2
    except LLMProviderError as exc:
        print(f"skilldistill: {exc}", file=sys.stderr)
        return 1
    skills_dir = args.output_dir
    if args.base_skill:
        collision = _source_collision(skills_dir, draft.name, [args.base_skill])
        if collision:
            print(
                f"skilldistill: refusing to overwrite the base skill: {collision}",
                file=sys.stderr,
            )
            return 2

    compare_dirs = [skills_dir, *args.compare_dir]
    seen_comparisons: set[Path] = set()
    for compare_dir in compare_dirs:
        resolved_compare_dir = Path(compare_dir).expanduser().resolve(strict=False)
        if resolved_compare_dir in seen_comparisons:
            continue
        seen_comparisons.add(resolved_compare_dir)
        for sim in find_similar(draft, resolved_compare_dir):
            print(
                "warning: similar existing skill: "
                f"{sim.name} ({sim.similarity:.0%}; {sim.path})",
                file=sys.stderr,
            )

    try:
        target = write_skill(draft, skills_dir, force=args.force)
    except (InvalidSkillNameError, SkillExistsError, UnsafeSkillPathError) as exc:
        print(f"skilldistill: {exc}", file=sys.stderr)
        return 1
    print(
        f"wrote {target}  (origin: {draft.origin}; "
        f"mode: {draft.mode}; sources: {draft.source_count})"
    )
    print("draft written for review; treat generated skills like third-party code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
