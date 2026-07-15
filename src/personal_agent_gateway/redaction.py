import os
import re
from collections.abc import Iterable
from typing import Any


_SENSITIVE_KEY = re.compile(r"token|key|secret|password|otp|recovery", re.IGNORECASE)
_BODY_KEYS = {"prompt", "message", "content", "stdout", "stderr", "file_content"}
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def environment_secrets() -> list[str]:
    return [
        value
        for key, value in os.environ.items()
        if value and _SENSITIVE_KEY.search(key)
    ]


def redact_text(value: object, *, secrets: Iterable[str] = (), limit: int = 2000) -> str:
    redacted = _PRIVATE_KEY.sub("[redacted-private-key]", str(value))
    for secret in [*environment_secrets(), *secrets]:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted[:limit]


def sanitize_metadata(value: dict[str, object], *, secrets: Iterable[str] = ()) -> dict[str, Any]:
    return {
        str(key): _sanitize(item, secrets)
        for key, item in value.items()
        if str(key).lower() not in _BODY_KEYS and not _SENSITIVE_KEY.search(str(key))
    }


def _sanitize(value: object, secrets: Iterable[str]) -> Any:
    if isinstance(value, dict):
        return sanitize_metadata({str(key): item for key, item in value.items()}, secrets=secrets)
    if isinstance(value, list):
        return [_sanitize(item, secrets) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return redact_text(value, secrets=secrets, limit=500) if isinstance(value, str) else value
    return redact_text(value, secrets=secrets, limit=500)
