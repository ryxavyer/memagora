"""CLI handlers for ``mempalace audit`` subactions.

Reads the local audit log (``~/.mempalace/audit.jsonl`` by default) and
surfaces entries to the engineer. v0.2 ships ``tail`` and ``export``;
``diff`` is deferred to v0.3 alongside the agora server.

Engineer sovereignty principle: this module ONLY reads the local log.
It never connects to the agora, never re-runs the classifier. The audit
log is the single source of truth for what crossed (or would have
crossed) the palace→agora boundary.
"""

import json
import sys
from pathlib import Path
from typing import Optional

from . import audit as _audit_mod


def run_audit(*, action: Optional[str], **kwargs) -> int:
    """Dispatch to ``_run_tail`` or ``_run_export``.

    Returns a process exit code (0 = success, 2 = unknown action).
    """
    if action == "tail":
        return _run_tail(limit=kwargs.get("limit", 10))
    if action == "export":
        return _run_export(output=kwargs.get("output"))
    # No action provided — surface help by signalling the caller to print.
    print("error: audit action required (tail | export)", file=sys.stderr)
    return 2


def _run_tail(*, limit: int) -> int:
    """Print the last ``limit`` entries, oldest first within the window.

    A small log is the common case — the audit log is one line per
    classified fact (and one per drawer write). At v0.2 scale there's
    no need for file-seek tricks; just read the whole thing and slice.

    Resolves the audit path via ``audit._default_audit_path()`` (module-
    qualified call, so monkeypatches in tests reach this code path too).
    """
    audit_path = _audit_mod._default_audit_path()
    entries = _audit_mod.read_audit_entries(audit_path)
    if not entries:
        print(f"(audit log is empty — {audit_path} does not exist or has no entries)")
        return 0

    window = entries[-limit:] if limit > 0 else entries
    for entry in window:
        print(_format_entry(entry))
    return 0


def _run_export(*, output: Optional[str]) -> int:
    """Dump the full audit log to ``output`` (or stdout if None)."""
    audit_path = _audit_mod._default_audit_path()
    entries = _audit_mod.read_audit_entries(audit_path)

    if output is None:
        for entry in entries:
            sys.stdout.write(json.dumps(entry, sort_keys=True, ensure_ascii=False))
            sys.stdout.write("\n")
        return 0

    out_path = Path(output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False))
            fh.write("\n")
    print(f"Wrote {len(entries)} entries to {out_path}")
    return 0


def _format_entry(entry: dict) -> str:
    """One-line pretty format for an audit entry.

    Two main shapes:
      - ``entry_type: "drawer_write"`` — palace storage event
      - ``entry_type: "classify"`` — classifier emitted a fact
    """
    entry_type = entry.get("entry_type", "?")
    dry = "[dry-run] " if entry.get("dry_run") else ""

    if entry_type == "classify":
        fact = entry.get("fact") or {}
        subj = fact.get("subject", "?")
        pred = fact.get("predicate", "?")
        obj = fact.get("object", "?")
        conf = fact.get("confidence", "?")
        session = entry.get("session_id") or "-"
        return f"{dry}classify  [{session}]  {subj} --{pred}--> {obj}  (conf={conf})"

    if entry_type == "drawer_write":
        op = entry.get("op", "?")
        doc_id = entry.get("id", "?")
        return f"{dry}drawer    [{op}]  id={doc_id}"

    # Unknown / future entry type — render as raw JSON
    return f"{dry}{entry_type}  " + json.dumps(entry, sort_keys=True, ensure_ascii=False)
