"""Content detectors for BopBop.

Runs patterns against inbound message bodies and the agent's outbound
responses. Returns a list of (detector, detail) hits. Never returns
or logs the body itself — we're deliberately preserving the privacy
of Note-to-Self traffic. A hit produces a `message.flagged` event
with only the detector name, score, and a short phrase that matched.
"""

from __future__ import annotations

import re

# Inbound detectors ----------------------------------------------------------

INJECTION_PHRASES = [
    r"\bignore (?:all )?previous (?:instructions?|prompts?)\b",
    r"\bforget (?:all|everything) (?:you|above)\b",
    r"\byou are now\b.{0,30}\b(?:developer|admin|root|god mode|jailbreak)\b",
    r"\bdisregard (?:the |your )?(?:system|rules|safety)\b",
    r"\bbypass (?:the |your )?(?:system|safety|filters?)\b",
    r"\[INST\]|\[/INST\]",
    r"<\|im_start\|>|<\|im_end\|>",
    r"</?\s*system\s*>",
]

_INJECTION_RE = re.compile("|".join(INJECTION_PHRASES), re.IGNORECASE)
_ROLE_TOKEN_RE = re.compile(r"^\s*(?:assistant|user|human|system)\s*:", re.MULTILINE | re.IGNORECASE)


def scan_inbound(text: str) -> list[tuple[str, str]]:
    """Return list of (detector, brief_reason). No body data leaks."""
    hits: list[tuple[str, str]] = []
    if not text:
        return hits

    if m := _INJECTION_RE.search(text):
        hits.append(("injection_phrase", m.group(0)[:40].lower()))

    # Role-impersonation: multiple role: prefixes in one message body is a
    # common way to try to inject an assistant turn.
    role_hits = _ROLE_TOKEN_RE.findall(text)
    if len(role_hits) >= 2:
        hits.append(("role_tokens", f"count={len(role_hits)}"))

    # Encoded blob: long, high-entropy, base64-alphabet-heavy segment.
    # Cheap heuristic: >600 chars in a single run of base64 chars.
    if re.search(r"[A-Za-z0-9+/]{600,}={0,2}", text):
        hits.append(("encoded_blob", "base64-like run"))

    return hits


# Outbound detectors (secret shapes in agent replies) -------------------------

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----")),
    # Dense .env-shaped content: 4+ KEY=VALUE lines in a row.
    ("env_dump", re.compile(r"(?:^[A-Z][A-Z0-9_]{2,}=[^\n]+\n){4,}", re.MULTILINE)),
]


def scan_outbound(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if not text:
        return hits
    for name, pat in SECRET_PATTERNS:
        m = pat.search(text)
        if m:
            # Reason is just the first 8 chars of the match — enough to
            # know which secret *type*, nowhere near enough to leak it.
            hits.append((name, f"prefix={m.group(0)[:8]}..."))
    return hits
