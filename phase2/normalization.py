"""Canonical normalization and secret redaction utilities for Phase 2."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_SPACE_RE = re.compile(r"\s+")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)

# Comprehensive Secret Redaction Patterns
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_AWS_SECRET_RE = re.compile(
    r"(?i)(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)[\s:=]+[\"']?[A-Za-z0-9/+=]{40}[\"']?"
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*\b"
)
_URL_CREDS_RE = re.compile(r"https?://([^:]+):([^@]+)@")
_DSN_CREDS_RE = re.compile(
    r"(?i)(postgres|postgresql|mysql|mongodb|redis|amqp)://([^:]+):([^@]+)@"
)
_PEM_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
)
_GENERIC_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|credentials?|private[_-]?key)[\s:=]+[\"']?[^\s\"',;]+[\"']?"
)


def redact_secrets(value: str) -> str:
    """Sanitize secrets, API keys, credentials, tokens, DSNs, and PEM keys from text."""
    if not value:
        return ""
    text = _PEM_KEY_RE.sub("[REDACTED_PEM_PRIVATE_KEY]", value)
    text = _AWS_KEY_RE.sub("[REDACTED_AWS_ACCESS_KEY]", text)
    text = _AWS_SECRET_RE.sub(r"\1=[REDACTED_AWS_SECRET]", text)
    text = _JWT_RE.sub("[REDACTED_JWT_TOKEN]", text)
    text = _URL_CREDS_RE.sub(r"http://\1:[REDACTED_PASSWORD]@", text)
    text = _DSN_CREDS_RE.sub(r"\1://\2:[REDACTED_PASSWORD]@", text)
    text = _GENERIC_SECRET_RE.sub(r"\1 <REDACTED>", text)
    return text


def redact_nested(value: Any) -> Any:
    """Recursively redact secrets from arbitrary Phase 3-bound structures.

    - Strings: apply ``redact_secrets``.
    - Dictionaries: recursively redact every value (keys are preserved).
    - Lists and tuples: recursively redact every item.
    - Numbers, booleans and null: preserved unchanged.

    The object is redacted in place structurally; it is never serialized to JSON
    and regex-scanned, because that would risk re-emitting redacted markers or
    mangling nested types.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_nested(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_nested(item) for item in value]
    return value


def normalize_template(value: str) -> str:
    """Return a stable canonical representation of log templates without destroying error semantics."""
    if not value:
        return ""
    text = redact_secrets(value.strip())
    text = _UUID_RE.sub("<UUID>", text)
    text = _HEX_RE.sub("<HEX>", text)
    return _SPACE_RE.sub(" ", text).casefold()


def incident_fingerprint(target_service: str, log_template: str) -> str:
    """Fingerprint the Phase 1 contract key: (service, log template)."""
    service = _SPACE_RE.sub(" ", target_service.strip()).casefold()
    template = normalize_template(log_template)
    return hashlib.sha256(f"{service}::{template}".encode("utf-8")).hexdigest()
