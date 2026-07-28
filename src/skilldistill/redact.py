"""Conservative, offline redaction for transcript text.

The helper intentionally has no provider or SDK dependency so callers can
redact likely credentials before logs or transcript excerpts leave the local
machine. It is a safety net, not a substitute for an allowlist-based data
egress policy.
"""

from __future__ import annotations

import re

DEFAULT_REDACTION = "[REDACTED]"

_SECRET_NAME = (
    r"(?:(?:[a-z0-9]+[-_])*(?:api[-_]?key|access[-_]?key(?:[-_]?id)?|"
    r"account[-_]?key|auth[-_]?token|client[-_]?secret|private[-_]?key|"
    r"secret(?:[-_]?key)?|password|passwd|token))"
)

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<kind>[A-Z0-9 ]*PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"
    r"(?:.*?-----END (?P=kind)-----|.*\Z)",
    re.DOTALL,
)
_DOUBLE_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>[\"']?{_SECRET_NAME}[\"']?\s*[:=]\s*)"
    r'(?P<quote>")(?P<value>(?:\\.|[^"\\\r\n])*)(?P=quote)',
    re.IGNORECASE,
)
_SINGLE_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>[\"']?{_SECRET_NAME}[\"']?\s*[:=]\s*)"
    r"(?P<quote>')(?P<value>(?:\\.|[^'\\\r\n])*)(?P=quote)",
    re.IGNORECASE,
)
_TRUNCATED_DOUBLE_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>[\"']?{_SECRET_NAME}[\"']?\s*[:=]\s*)"
    r'(?P<quote>")(?P<value>(?:\\.|[^"\\\r\n])*)(?=\r?$)',
    re.IGNORECASE | re.MULTILINE,
)
_TRUNCATED_SINGLE_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>[\"']?{_SECRET_NAME}[\"']?\s*[:=]\s*)"
    r"(?P<quote>')(?P<value>(?:\\.|[^'\\\r\n])*)(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_UNQUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>\b{_SECRET_NAME}\b\s*[:=]\s*)"
    r"(?P<value>[^\s,;&]+)",
    re.IGNORECASE,
)
_DOUBLE_QUOTED_COMMAND_ARGUMENT = re.compile(
    rf"(?P<prefix>--{_SECRET_NAME}(?:\s+|=))"
    r'(?P<quote>")(?P<value>(?:\\.|[^"\\\r\n])*)(?P=quote)',
    re.IGNORECASE,
)
_SINGLE_QUOTED_COMMAND_ARGUMENT = re.compile(
    rf"(?P<prefix>--{_SECRET_NAME}(?:\s+|=))"
    r"(?P<quote>')(?P<value>(?:\\.|[^'\\\r\n])*)(?P=quote)",
    re.IGNORECASE,
)
_TRUNCATED_DOUBLE_QUOTED_COMMAND_ARGUMENT = re.compile(
    rf"(?P<prefix>--{_SECRET_NAME}(?:\s+|=))"
    r'(?P<quote>")(?P<value>(?:\\.|[^"\\\r\n])*)(?=\r?$)',
    re.IGNORECASE | re.MULTILINE,
)
_TRUNCATED_SINGLE_QUOTED_COMMAND_ARGUMENT = re.compile(
    rf"(?P<prefix>--{_SECRET_NAME}(?:\s+|=))"
    r"(?P<quote>')(?P<value>(?:\\.|[^'\\\r\n])*)(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_UNQUOTED_COMMAND_ARGUMENT = re.compile(
    rf"(?P<prefix>--{_SECRET_NAME}(?:\s+|=))"
    r"(?P<value>[^\s\"']+)",
    re.IGNORECASE,
)
_BASIC_AUTH = re.compile(
    r"(?P<prefix>\b(?:Proxy-)?Authorization\s*:\s*Basic\s+)"
    r"(?P<value>[A-Za-z0-9+/=]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(
    r"(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_URL_CREDENTIAL = re.compile(
    r"(?P<prefix>(?:https?|postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|"
    r"redis|amqps?)://[^:/@\s]+:)"
    r"(?P<value>[^@/\s]+)(?=@)",
    re.IGNORECASE,
)
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:proj-|ant-api\d{2}-)?[A-Za-z0-9_-]{16,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"npm_[A-Za-z0-9]{20,}|"
    r"key_[A-Za-z0-9]{20,}|"
    r"(?:sk|rk)_live_[A-Za-z0-9]{16,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")(?![A-Za-z0-9])"
)


def _replace_value(match: re.Match[str], replacement: str) -> str:
    quote = match.groupdict().get("quote", "")
    return f"{match.group('prefix')}{quote}{replacement}{quote}"


def _replace_unquoted_assignment(match: re.Match[str], replacement: str) -> str:
    prefix = match.group("prefix")
    if prefix.rstrip().endswith(":"):
        escaped = replacement.replace("\\", "\\\\").replace('"', '\\"')
        return f'{prefix}"{escaped}"'
    return f"{prefix}{replacement}"


def redact_text(text: str, replacement: str = DEFAULT_REDACTION) -> str:
    """Return ``text`` with common credential forms replaced.

    Redaction covers labelled assignments, command flags, bearer credentials,
    passwords embedded in URLs, private-key blocks, and common vendor token
    formats. The operation is deterministic and leaves ordinary prose intact.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(replacement, str):
        raise TypeError("replacement must be a string")

    redacted = _PRIVATE_KEY.sub(lambda match: replacement, text)
    redacted = _DOUBLE_QUOTED_ASSIGNMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _SINGLE_QUOTED_ASSIGNMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _TRUNCATED_DOUBLE_QUOTED_ASSIGNMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _TRUNCATED_SINGLE_QUOTED_ASSIGNMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _DOUBLE_QUOTED_COMMAND_ARGUMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _SINGLE_QUOTED_COMMAND_ARGUMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _TRUNCATED_DOUBLE_QUOTED_COMMAND_ARGUMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _TRUNCATED_SINGLE_QUOTED_COMMAND_ARGUMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _UNQUOTED_COMMAND_ARGUMENT.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _UNQUOTED_ASSIGNMENT.sub(
        lambda match: _replace_unquoted_assignment(match, replacement), redacted
    )
    redacted = _BEARER_TOKEN.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _BASIC_AUTH.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    redacted = _URL_CREDENTIAL.sub(
        lambda match: _replace_value(match, replacement), redacted
    )
    return _KNOWN_TOKEN.sub(lambda match: replacement, redacted)
