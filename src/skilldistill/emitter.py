"""Write skill drafts to a skills directory."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from skilldistill.distill import SkillDraft

_SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class SkillExistsError(FileExistsError):
    pass


class InvalidSkillNameError(ValueError):
    pass


class UnsafeSkillPathError(ValueError):
    pass


def _validate_skill_name(name: str) -> None:
    if not isinstance(name, str):
        raise InvalidSkillNameError("skill name must be a string")
    if not 1 <= len(name) <= 64:
        raise InvalidSkillNameError("skill name must contain between 1 and 64 characters")
    if not _SKILL_NAME.fullmatch(name):
        raise InvalidSkillNameError(
            "skill name must contain lowercase letters, numbers, and single hyphens only"
        )
    if name in _WINDOWS_RESERVED_NAMES:
        raise InvalidSkillNameError(f"skill name is not portable across platforms: {name}")


def _require_contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise UnsafeSkillPathError(f"refusing to write outside skills directory: {path}") from exc


def _safe_target(name: str, skills_dir: Path | str) -> Path:
    _validate_skill_name(name)

    requested_root = Path(skills_dir).expanduser()
    requested_root.mkdir(parents=True, exist_ok=True)
    root = requested_root.resolve(strict=True)

    requested_parent = requested_root / name
    if requested_parent.is_symlink():
        raise UnsafeSkillPathError(f"skill directory must not be a symlink: {requested_parent}")
    _require_contained(requested_parent.resolve(strict=False), root)

    requested_parent.mkdir(parents=False, exist_ok=True)
    if requested_parent.is_symlink():
        raise UnsafeSkillPathError(f"skill directory must not be a symlink: {requested_parent}")
    parent = requested_parent.resolve(strict=True)
    _require_contained(parent, root)

    target = parent / "SKILL.md"
    if target.is_symlink():
        raise UnsafeSkillPathError(f"skill file must not be a symlink: {target}")
    _require_contained(target.resolve(strict=False), root)
    return target


def _sync_directory(path: Path) -> None:
    """Best-effort directory sync so a completed rename is durable on POSIX."""
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | directory_flag)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _require_stable_directory(path: Path) -> None:
    """Reject a directory replaced with a symlink after target validation."""
    try:
        current = path.resolve(strict=True)
    except OSError as exc:
        raise UnsafeSkillPathError(f"skill directory changed while writing: {path}") from exc
    if current != path:
        raise UnsafeSkillPathError(f"skill directory changed while writing: {path}")


def _atomic_write(target: Path, content: str, force: bool) -> None:
    # _safe_target returns a canonical absolute parent even when skills_dir was
    # relative. Check it both before and after staging to narrow symlink races.
    _require_stable_directory(target.parent)
    if (target.exists() or target.is_symlink()) and not force:
        raise SkillExistsError(f"{target} already exists (use force to overwrite)")

    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        # New drafts retain mkstemp's private 0600 mode. On replacement, restore
        # the prior mode only after sensitive content has been fully staged.
        target_mode = (
            stat.S_IMODE(target.stat().st_mode)
            if target.exists() and not target.is_symlink()
            else None
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if target_mode is not None:
            os.chmod(temporary, target_mode)

        _require_stable_directory(target.parent)

        if force:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise SkillExistsError(
                    f"{target} already exists (use force to overwrite)"
                ) from exc
            temporary.unlink()
        _sync_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def write_skill(
    draft: SkillDraft,
    skills_dir: Path | str,
    force: bool = False,
) -> Path:
    target = _safe_target(draft.name, skills_dir)
    _atomic_write(target, draft.content, force=force)
    return target
