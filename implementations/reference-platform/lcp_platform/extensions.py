"""LEP-0006 Track B receiver policy for `lead`/`post` ``extensions``.

The wire surface (``schemas/lead.json``, ``schemas/post.json``) enforces the
key namespace format and forbids ``lcp.core.*``; everything a namespace
*means* is a receiver rule, per LEP-0006 §B.1's boundary statement. This
module is the reference platform's receiver implementation of the two rules
the record does place on receivers:

* **§B.2 registered shape** — ``{org}.form.questions`` is at most 25 entries of
  ``question_id`` / ``question_text`` / ``answer_text`` (from text 300 chars,
  answer 1000, no other keys). A receiver that recognizes the namespace MUST
  validate entries and accept or reject the message; it MUST NOT silently
  store a non-conforming array. The platform validates every
  ``*.form.questions`` namespace: the registered family is ``{org}``-open, and
  the shape is normative for any org that registers it.
* **§B.3.4 PII discipline** — question/answer text can smuggle consumer
  contact values that hash-keyed suppression/erasure cannot see. The
  publisher MUST keep them out; a platform MAY redact or reject. This build
  takes the strict reading at **lead ingest: reject** (``LCP-100``), because
  accepting would persist free-text contacts in the lead record (the §9
  retention scope the record says ``extensions`` inherits). At **post
  emission it redacts** instead of rejecting — a lead that was already
  accepted must not strand a buyer over a stale value, and every redaction is
  logged against the ``lead_id`` (§B.3.4 "MUST log each redaction").

Unrecognized namespaces are not shape-checked (extension
forward-compatibility: ignore what you do not register), but the PII walk
still covers their string values — the class rule is not namespace-specific.
Values are never rewritten on the lead: the stored intake record is either
conforming or was rejected.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any

from .pii import has_plaintext_contact, redact_plaintext_contacts

FORM_QUESTIONS_SUFFIX = ".form.questions"
FORM_QUESTIONS_MAX_ENTRIES = 25
FORM_QUESTION_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
FORM_QUESTION_TEXT_MAX = 300
FORM_ANSWER_TEXT_MAX = 1000
FORM_QUESTION_KEYS = frozenset({"question_id", "question_text", "answer_text"})


def extension_bytes(extensions: Mapping[str, Any]) -> int:
    """Compact UTF-8 bytes of the extension object (the canonical wire form)."""

    return len(
        json.dumps(extensions, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


def _iter_string_values(extensions: Any, root: str) -> list[tuple[str, str]]:
    """Every string value in the object with its wire path (iterative walk)."""

    found: list[tuple[str, str]] = []
    stack: list[tuple[Any, str]] = [(extensions, root)]
    while stack:
        node, path = stack.pop()
        if isinstance(node, Mapping):
            for key, value in node.items():
                stack.append((value, f"{path}.{key}"))
            continue
        if isinstance(node, list):
            stack.extend(
                (value, f"{path}[{index}]") for index, value in enumerate(node)
            )
            continue
        if isinstance(node, str):
            found.append((path, node))
    found.sort()
    return found


def _form_questions_problems(namespace: str, value: Any) -> list[str]:
    path = f"extensions.{namespace}"
    if not isinstance(value, list):
        return [f"{path} must be an array of question objects (LEP-0006 B.2)"]
    problems: list[str] = []
    if len(value) > FORM_QUESTIONS_MAX_ENTRIES:
        problems.append(
            f"{path} carries {len(value)} entries; the registered shape allows "
            f"at most {FORM_QUESTIONS_MAX_ENTRIES}"
        )
    for index, entry in enumerate(value):
        entry_path = f"{path}[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{entry_path} must be a question object")
            continue
        unknown = sorted(set(entry) - FORM_QUESTION_KEYS)
        if unknown:
            problems.append(
                f"{entry_path} carries keys outside the registered shape: "
                + ", ".join(unknown)
            )
        question_id = entry.get("question_id")
        if question_id is not None and not (
            isinstance(question_id, str) and FORM_QUESTION_ID_RE.match(question_id)
        ):
            problems.append(
                f"{entry_path}.question_id must match ^[a-z0-9][a-z0-9_-]{{0,63}}$"
            )
        question_text = entry.get("question_text")
        if not isinstance(question_text, str):
            problems.append(f"{entry_path}.question_text is required")
        elif len(question_text) > FORM_QUESTION_TEXT_MAX:
            problems.append(
                f"{entry_path}.question_text is {len(question_text)} characters; "
                f"the registered shape allows at most {FORM_QUESTION_TEXT_MAX}"
            )
        answer_text = entry.get("answer_text")
        if not isinstance(answer_text, str):
            problems.append(f"{entry_path}.answer_text is required")
        elif len(answer_text) > FORM_ANSWER_TEXT_MAX:
            problems.append(
                f"{entry_path}.answer_text is {len(answer_text)} characters; "
                f"the registered shape allows at most {FORM_ANSWER_TEXT_MAX}"
            )
    return problems


def extension_problems(
    extensions: Mapping[str, Any], *, max_total_bytes: int
) -> list[str]:
    """Every receiver-policy violation in an ``extensions`` object.

    Covers the §B.2 registered shape for the ``*.form.questions`` family, the
    §B.3.4 PII class rule, and the deployment's total-size policy bound. An
    empty list means the object may be stored and matched.
    """

    problems: list[str] = []
    size = extension_bytes(extensions)
    if size > max_total_bytes:
        problems.append(
            f"extensions serialize to {size} bytes, above this receiver's "
            f"{max_total_bytes}-byte policy bound (LEP-0006 B.2: the 32 KiB "
            "soft target is a scan-cheap policy bound, not a shape ceiling)"
        )
    for namespace, value in sorted(extensions.items()):
        if namespace.endswith(FORM_QUESTIONS_SUFFIX):
            problems.extend(_form_questions_problems(namespace, value))
    for path, value in _iter_string_values(extensions, "extensions"):
        if has_plaintext_contact(value):
            problems.append(
                f"{path} carries a plaintext contact value; extension text must "
                "not duplicate contact data that hash-keyed suppression and "
                "erasure cannot reach (LEP-0006 B.3.4)"
            )
    return problems


def redact_extension_pii(
    extensions: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Copy ``extensions`` with every plaintext contact span redacted.

    Returns ``(clean_extensions, redacted_paths)``. Only contact spans are
    replaced, so non-identifier answer text survives; a caller that sees
    paths MUST log a redaction event against the lead (§B.3.4). Shape bounds
    are not re-checked here: a post is emitted from a lead that passed the
    ingest shape rule.
    """

    clean = copy.deepcopy(dict(extensions))
    redacted: list[str] = []
    stack: list[tuple[Any, str]] = [(clean, "extensions")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in sorted(node.items()):
                child = f"{path}.{key}"
                if isinstance(value, str):
                    replacement, count = redact_plaintext_contacts(value)
                    if count:
                        node[key] = replacement
                        redacted.append(child)
                elif isinstance(value, (dict, list)):
                    stack.append((value, child))
            continue
        if isinstance(node, list):
            for index, value in enumerate(node):
                child = f"{path}[{index}]"
                if isinstance(value, str):
                    replacement, count = redact_plaintext_contacts(value)
                    if count:
                        node[index] = replacement
                        redacted.append(child)
                elif isinstance(value, (dict, list)):
                    stack.append((value, child))
    redacted.sort()
    return clean, redacted
