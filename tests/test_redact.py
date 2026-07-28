import pytest

from skilldistill.redact import DEFAULT_REDACTION, redact_text


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-" + "a" * 24,
        "sk-ant-api03-" + "b" * 24,
        "ghp_" + "c" * 30,
        "github_pat_" + "d" * 30,
        "glpat-" + "e" * 24,
        "xoxb-1234567890-abcdefghijk",
        "AKIA" + "F" * 16,
        "AIza" + "g" * 31,
        "npm_" + "h" * 24,
        "key_" + "i" * 24,
        "sk_live_" + "j" * 24,
        "eyJabcdefgh.abcdefghijk.abcdefghijk",
    ],
)
def test_redacts_common_vendor_token_formats(secret: str):
    result = redact_text(f"credential before={secret} after")

    assert secret not in result
    assert DEFAULT_REDACTION in result


def test_redacts_labelled_assignments_and_command_arguments():
    text = """
OPENAI_API_KEY=short-env-secret
"client_secret": "json-secret"
password: hunter2
run --api-key command-secret --verbose
https://service.test/callback?token=query-secret&next=yes
"""

    result = redact_text(text)

    for secret in (
        "short-env-secret",
        "json-secret",
        "hunter2",
        "command-secret",
        "query-secret",
    ):
        assert secret not in result
    assert "OPENAI_API_KEY=[REDACTED]" in result
    assert '"client_secret": "[REDACTED]"' in result
    assert 'password: "[REDACTED]"' in result
    assert "--api-key [REDACTED]" in result
    assert "next=yes" in result


def test_redacts_quoted_secrets_with_spaces_quotes_and_escapes():
    text = (
        "\"client_secret\": \"abc'def\"\n"
        '\"api_key\": \"abc\\\\\\\"defghi\"\n'
        '--password "alpha beta"\n'
        "--token 'single quoted value'"
    )

    result = redact_text(text)

    for secret_fragment in ("abc'def", "defghi", "alpha beta", "single quoted value"):
        assert secret_fragment not in result
    assert result.count(DEFAULT_REDACTION) == 4


def test_truncated_quoted_secrets_fail_closed_at_end_of_line():
    text = 'API_KEY="alpha beta\n--password \'secret words'

    result = redact_text(text)

    assert "alpha beta" not in result
    assert "secret words" not in result
    assert result.count(DEFAULT_REDACTION) == 2


def test_redacts_bearer_tokens_and_url_passwords():
    text = (
        "Authorization: Bearer abc.def-123_456\n"
        "DATABASE_URL=postgresql://service-user:p%40ssword@db.internal/app"
    )

    result = redact_text(text)

    assert "abc.def-123_456" not in result
    assert "p%40ssword" not in result
    assert "Bearer [REDACTED]" in result
    assert "postgresql://service-user:[REDACTED]@db.internal/app" in result


def test_redacts_basic_and_proxy_authorization_headers():
    text = (
        "Authorization: Basic dXNlcjpwYXNzd29yZA==\n"
        "Proxy-Authorization: Basic cHJveHk6c2VjcmV0"
    )

    result = redact_text(text)

    assert "dXNlcjpwYXNzd29yZA==" not in result
    assert "cHJveHk6c2VjcmV0" not in result
    assert result.count(DEFAULT_REDACTION) == 2


def test_redacts_complete_private_key_block():
    private_key = """-----BEGIN PRIVATE KEY-----
super-sensitive-material
second-line
-----END PRIVATE KEY-----"""

    result = redact_text(f"before\n{private_key}\nafter")

    assert "super-sensitive-material" not in result
    assert "BEGIN PRIVATE KEY" not in result
    assert result == "before\n[REDACTED]\nafter"


@pytest.mark.parametrize(
    "private_key",
    [
        "-----BEGIN PRIVATE KEY-----\ntruncated-sensitive-material",
        (
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
            "pgp-sensitive-material\n"
            "-----END PGP PRIVATE KEY BLOCK-----"
        ),
        (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "mismatched-sensitive-material\n"
            "-----END PRIVATE KEY-----"
        ),
    ],
)
def test_private_key_redaction_fails_closed(private_key: str):
    result = redact_text(f"before\n{private_key}")

    assert "sensitive-material" not in result
    assert result == "before\n[REDACTED]"


def test_leaves_ordinary_prose_unchanged():
    text = (
        "Keep the token budget small. Follow the password policy. "
        "This is a secret sauce recipe, and sk-short is not a credential."
    )

    assert redact_text(text) == text


def test_custom_replacement_is_deterministic_and_idempotent():
    text = "api_key=local-development-secret"
    once = redact_text(text, replacement="<hidden>")

    assert once == "api_key=<hidden>"
    assert redact_text(once, replacement="<hidden>") == once


def test_custom_replacement_is_treated_as_literal_text():
    assert redact_text("token=secret", replacement=r"\hidden") == r"token=\hidden"


@pytest.mark.parametrize("value", [None, b"bytes", 123])
def test_rejects_non_string_input(value):
    with pytest.raises(TypeError, match="text must be a string"):
        redact_text(value)
