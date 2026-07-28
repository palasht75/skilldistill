"""skilldistill CLI: scan sessions, distill skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skilldistill import __version__
from skilldistill.dedup import find_similar
from skilldistill.distill import distill
from skilldistill.emitter import SkillExistsError, write_skill
from skilldistill.llm import resolve_llm
from skilldistill.transcripts import find_sessions, parse_session

DEFAULT_SESSIONS = "~/.claude/projects"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="skilldistill", description="Turn successful agent sessions into reusable skills."
    )
    parser.add_argument("--version", action="version", version=f"skilldistill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="rank sessions worth distilling")
    p_scan.add_argument("root", nargs="?", default=DEFAULT_SESSIONS,
                        help=f"sessions dir (default: {DEFAULT_SESSIONS})")
    p_scan.add_argument("--min-score", type=float, default=0.5)
    p_scan.add_argument("--limit", type=int, default=20, help="max sessions to inspect")
    p_scan.add_argument("--json", action="store_true")

    p_dist = sub.add_parser("distill", help="distill one session into a SKILL.md draft")
    p_dist.add_argument("session", help="path to a session .jsonl")
    p_dist.add_argument("--skills-dir", default="skills")
    p_dist.add_argument("--offline", action="store_true", help="skip LLM, emit outline draft")
    p_dist.add_argument("--force", action="store_true", help="overwrite an existing skill")

    args = parser.parse_args(argv)

    if args.command == "scan":
        from skilldistill.detect import score_session

        root = Path(args.root).expanduser()
        if not root.exists():
            print(f"skilldistill: sessions dir not found: {root}", file=sys.stderr)
            return 2
        rows = []
        for path in find_sessions(root)[: args.limit]:
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

    # distill
    session_path = Path(args.session).expanduser()
    if not session_path.exists():
        print(f"skilldistill: session not found: {session_path}", file=sys.stderr)
        return 2
    session = parse_session(session_path)
    llm = None if args.offline else resolve_llm()
    if llm is None and not args.offline:
        print(
            "note: no LLM configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY and install "
            "the matching extra) — emitting offline outline draft",
            file=sys.stderr,
        )
    draft = distill(session, llm=llm)

    for sim in find_similar(draft, args.skills_dir):
        print(f"warning: similar existing skill: {sim.name} ({sim.similarity:.0%})", file=sys.stderr)

    try:
        target = write_skill(draft, args.skills_dir, force=args.force)
    except SkillExistsError as exc:
        print(f"skilldistill: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {target}  (origin: {draft.origin})")
    print("review the draft before installing it — treat generated skills like third-party code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
