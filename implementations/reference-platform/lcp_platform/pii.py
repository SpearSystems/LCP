"""Shape-based plaintext contact detection (SPEC §4 class rule, #198).

One shape authority for every surface that must keep consumer contact values
out of places the hash-keyed suppression/erasure flows cannot reach. It is
used by the event ``details`` ingest validator (router) and by the LEP-0006
Track B extension guard (``extensions.py``), so the two rules cannot drift
apart or disagree about what a phone number looks like.

The check is deliberately shape-based, not inferential — no DLP scoring, no
normalization of the value it refuses.
"""

from __future__ import annotations

import re

# An email address, or a phone-number RUN inside a longer string. Boundaries
# are the point: a run may not start or end inside an identifier — letters,
# digits, `_`, `-`, `.` and `/` are all identifier material, so
# `ping_550e8400-1`, `OPP-2026-0001234`, `v1.1.0` and the `2026-11-02` tail of
# an ISO timestamp cannot match, while a run surrounded by whitespace or
# punctuation is examined. The match length is bounded, so a long opaque id
# cannot match either. Digests and ids are 64-hex/opaque strings and are
# unaffected; dates are excluded explicitly (a date is not a phone number).
EMAIL_SHAPED_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
PHONE_RUN_RE = re.compile(r"(?<![\w./-])\+?\(?\d[\d\s().\-]{4,19}\d(?![\w./-])")
DATE_ONLY_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15

REDACTION_MARKER = "[REDACTED]"


def plaintext_contact_spans(value: str) -> list[tuple[int, int]]:
    """Every span of ``value`` holding a plaintext contact shape, in order."""

    spans: list[tuple[int, int]] = [
        match.span() for match in EMAIL_SHAPED_RE.finditer(value)
    ]
    for match in PHONE_RUN_RE.finditer(value):
        candidate = match.group(0)
        if DATE_ONLY_RE.match(candidate):
            continue
        digits = sum(1 for char in candidate if "0" <= char <= "9")
        if PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS:
            spans.append(match.span())
    spans.sort()
    return spans


def has_plaintext_contact(value: str) -> bool:
    """Whether a string carries contact data in the clear."""

    return bool(plaintext_contact_spans(value))


def redact_plaintext_contacts(value: str) -> tuple[str, int]:
    """Replace each detected contact span with ``[REDACTED]``.

    Returns ``(redacted_value, redaction_count)``; the count lets the caller
    log one redaction event per removed value, deterministically. Text around
    the span is preserved (``Call Jane at 0412 345 678`` becomes
    ``Call Jane at [REDACTED]``), so a delivered answer keeps its meaning
    while losing the identifier.
    """

    spans = plaintext_contact_spans(value)
    if not spans:
        return value, 0
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(value[cursor:start])
        pieces.append(REDACTION_MARKER)
        cursor = end
    pieces.append(value[cursor:])
    return "".join(pieces), len(spans)
