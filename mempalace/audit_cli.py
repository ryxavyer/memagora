"""CLI handlers for ``mempalace audit`` subactions.

Reads the local audit log (``~/.mempalace/audit.jsonl`` by default) and
surfaces entries to the engineer. ``tail`` and ``export`` shipped in v0.2;
``diff`` arrives in v0.3 now that there is a live agora to compare against.

Engineer sovereignty principle: ``tail`` and ``export`` ONLY read the local
log — they never connect to the agora and never re-run the classifier. The
audit log is the single source of truth for what crossed (or would have
crossed) the palace→agora boundary.

``diff`` is the one exception, and it is deliberate: the engineer explicitly
asks "does the agora hold what my log says I sent?", which cannot be answered
without asking the agora. It reads; it never writes.
"""

import json
import sys
from pathlib import Path
from typing import Optional

from . import audit as _audit_mod


def run_audit(*, action: Optional[str], **kwargs) -> int:
    """Dispatch to ``_run_tail``, ``_run_export`` or ``_run_diff``.

    Returns a process exit code (0 = success, 2 = unknown action).
    """
    if action == "tail":
        return _run_tail(limit=kwargs.get("limit", 10))
    if action == "export":
        return _run_export(output=kwargs.get("output"))
    if action == "diff":
        return _run_diff(strict=kwargs.get("strict", False), limit=kwargs.get("limit", 500))
    # No action provided — surface help by signalling the caller to print.
    print("error: audit action required (tail | export | diff)", file=sys.stderr)
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


def _run_diff(*, strict: bool, limit: int) -> int:
    """Compare the local audit log against what the agora actually holds.

    The audit log is the engineer's record of what should have crossed. This
    asks the server what did, and reports the difference:

      * **local only** — classified here but not in the agora. Expected while
        dry-run is on; otherwise it means a POST failed (look for a ``post``
        entry with ``ok: false``) or the fact was rejected as a duplicate.
      * **agora only** — facts teammates contributed. Not a problem; shown
        because "what does the team know that I didn't tell it" is useful.

    Comparison is on the ``(subject, predicate, object)`` triple, not on
    provenance: the same decision recorded by two engineers is one fact in the
    agora, and matching on session ids would report it as missing.

    This is the one read-side network call on the engineer side, and it is
    explicitly invoked. Exit codes: 0 success, 2 could not reach the agora,
    and with ``--strict``, 1 when anything is local-only.
    """
    from .config_agora import load_agora_config

    cfg = load_agora_config()
    if not cfg.enabled:
        print("No agora endpoint configured — nothing to diff against.")
        print("Set MEMPALACE_AGORA_ENDPOINT (see docs/deployment.md).")
        return 0

    audit_path = _audit_mod._default_audit_path()
    local = _local_triples(_audit_mod.read_audit_entries(audit_path))

    remote = _remote_triples(cfg, limit=limit)
    if remote is None:
        print(f"error: could not reach the agora at {cfg.endpoint}", file=sys.stderr)
        return 2

    local_only = sorted(local - remote)
    agora_only = sorted(remote - local)
    matched = len(local & remote)

    print(f"local audit log : {audit_path}")
    print(f"agora           : {cfg.endpoint}")
    if cfg.dry_run:
        print("mode            : dry-run (nothing has been posted from this machine)")
    print("")
    print(f"matched         : {matched}")
    print(f"local only      : {len(local_only)}")
    print(f"agora only      : {len(agora_only)}")

    if local_only:
        print("\nClassified locally, not in the agora:")
        for triple in local_only:
            print(f"  {_format_triple(triple)}")
    if agora_only:
        print("\nIn the agora, not classified on this machine:")
        for triple in agora_only:
            print(f"  {_format_triple(triple)}")

    if strict and local_only:
        return 1
    return 0


def _local_triples(entries: list) -> set:
    """Every triple the classifier emitted, from ``classify`` entries."""
    triples = set()
    for entry in entries:
        if entry.get("entry_type") != "classify":
            continue
        fact = entry.get("fact") or {}
        subject, predicate, obj = (
            fact.get("subject"),
            fact.get("predicate"),
            fact.get("object"),
        )
        if subject and predicate and obj:
            triples.add(_normalize_triple(subject, predicate, obj))
    return triples


def _remote_triples(cfg, *, limit: int) -> Optional[set]:
    """Page through the agora's facts. ``None`` when the server can't be reached."""
    from .client import get_facts

    triples = set()
    cursor = None
    fetched = 0
    while fetched < limit:
        page = get_facts(
            endpoint=cfg.endpoint,
            api_key=cfg.api_key,
            limit=min(100, limit - fetched),
            cursor=cursor,
            timeout=cfg.post_timeout,
        )
        if page is None:
            return None
        for fact in page.facts:
            triples.add(_normalize_triple(fact.subject, fact.predicate, fact.object))
        fetched += len(page.facts)
        cursor = page.next_cursor
        if not cursor:
            break
    return triples


def _normalize_triple(subject: str, predicate: str, obj: str) -> tuple:
    """Apply the server's normalization so both sides compare like for like."""
    return (
        subject.strip(),
        predicate.strip().lower().replace(" ", "_"),
        obj.strip(),
    )


def _format_triple(triple: tuple) -> str:
    subject, predicate, obj = triple
    return f"{subject} --{predicate}--> {obj}"


def _format_entry(entry: dict) -> str:
    """One-line pretty format for an audit entry.

    Three shapes:
      - ``entry_type: "drawer_write"`` — palace storage event
      - ``entry_type: "classify"`` — classifier emitted a fact
      - ``entry_type: "post"`` — a batch was sent to the agora (v0.3)
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

    if entry_type == "post":
        session = entry.get("session_id") or "-"
        status = "ok" if entry.get("ok") else "FAILED"
        detail = f" — {entry['message']}" if entry.get("message") else ""
        return (
            f"post      [{session}]  {entry.get('endpoint', '?')}  "
            f"{status}: accepted={entry.get('accepted', '?')} "
            f"rejected={entry.get('rejected', '?')}{detail}"
        )

    if entry_type == "drawer_write":
        op = entry.get("op", "?")
        doc_id = entry.get("id", "?")
        return f"{dry}drawer    [{op}]  id={doc_id}"

    # Unknown / future entry type — render as raw JSON
    return f"{dry}{entry_type}  " + json.dumps(entry, sort_keys=True, ensure_ascii=False)
