"""Append-only audit log of every classified fact MemAgora considers
sending to the team agora server.

Engineer sovereignty principle (see AGENTS.md): every fact that crosses
the palace→agora boundary — even in dry-run mode — is mirrored locally
so the engineer can inspect what left their machine. This module owns
that local mirror.

Format: one JSON object per line, UTF-8. Append-only — never rewritten.
The default location is ``~/.mempalace/audit.jsonl``.

This module is intentionally tiny; it is loaded eagerly by
``backend_agora`` and must not pull in network dependencies.
"""

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional


DEFAULT_AUDIT_FILENAME = "audit.jsonl"


def _default_audit_path() -> Path:
    return Path.home() / ".mempalace" / DEFAULT_AUDIT_FILENAME


def write_audit_entry(
    entry: Mapping[str, Any],
    *,
    audit_path: Optional[Path] = None,
) -> None:
    """Append ``entry`` as a single JSON line to ``audit_path``.

    Creates the parent directory and the file on first call. Uses
    ``ensure_ascii=False`` so non-ASCII content stays human-readable.

    Idempotency: callers may pass overlapping entries — this function
    does not deduplicate. The audit log is append-only by design.
    """
    if audit_path is None:
        audit_path = _default_audit_path()
    else:
        audit_path = Path(audit_path)

    audit_path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    # Use plain "a" mode — JSONL doesn't need line buffering tricks at
    # the scale this log operates (one entry per palace write).
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.write(os.linesep if os.name == "nt" else "\n")


def read_audit_entries(audit_path: Optional[Path] = None) -> list[dict]:
    """Read every entry back as a list of dicts.

    Returns ``[]`` when the file does not exist. Used by the
    ``memagora audit`` CLI (v0.2) and by tests; not by the write path.
    """
    if audit_path is None:
        audit_path = _default_audit_path()
    else:
        audit_path = Path(audit_path)

    if not audit_path.exists():
        return []

    entries = []
    with open(audit_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries
