"""``agora-admin`` — the operator's CLI.

Everything an operator needs that is not an HTTP request: applying migrations,
issuing and revoking engineer keys, and moving facts between storage backends.

The export/import pair is the migration story for a team that outgrows (or
simply dislikes) the reference Postgres backend:

    AGORA_STORE=postgres AGORA_DSN=... agora-admin export --output facts.jsonl
    AGORA_STORE=sqlite   AGORA_SQLITE_PATH=... agora-admin migrate
    AGORA_STORE=sqlite   AGORA_SQLITE_PATH=... agora-admin import facts.jsonl

Facts keep their ids, ingest timestamps, and per-engineer provenance across the
move — the import path is not a re-ingest.
"""

import argparse
import dataclasses
import json
import sys
from typing import Optional

from .auth import generate_key
from .config import load_config
from .storage import AgoraStoreError, build_store
from .storage.base import StoredFact


def cmd_migrate(store, config, args) -> int:
    applied = store.migrate()
    if applied:
        print(f"applied migrations: {', '.join(applied)}")
    else:
        print("schema is up to date")
    return 0


def cmd_issue_key(store, config, args) -> int:
    deployment = args.deployment or config.deployment_id
    key, record = generate_key(deployment_id=deployment, engineer_id=args.engineer)
    store.put_api_key(record=record)

    # The secret exists in exactly one place after this line: the operator's
    # terminal. Only its hash reaches the database.
    print(f"key id     : {record.key_id}")
    print(f"engineer   : {record.engineer_id}")
    print(f"deployment : {record.deployment_id}")
    print("")
    print("Give this to the engineer — it is not recoverable:")
    print(key)
    return 0


def cmd_revoke_key(store, config, args) -> int:
    if store.revoke_api_key(key_id=args.key_id):
        print(f"revoked {args.key_id}")
        return 0
    print(f"no active key with id {args.key_id}", file=sys.stderr)
    return 1


def cmd_list_keys(store, config, args) -> int:
    deployment = args.deployment or config.deployment_id
    records = store.list_api_keys(deployment_id=deployment)
    if not records:
        print(f"(no keys issued for deployment {deployment})")
        return 0
    for record in records:
        state = "active" if record.active else f"revoked {record.revoked_at}"
        print(f"{record.key_id}  {record.engineer_id:<20}  {record.created_at}  {state}")
    return 0


def cmd_export(store, config, args) -> int:
    deployment = args.deployment or config.deployment_id
    handle = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    count = 0
    try:
        for fact in store.export_facts(deployment_id=deployment):
            handle.write(json.dumps(dataclasses.asdict(fact), ensure_ascii=False) + "\n")
            count += 1
    finally:
        if args.output:
            handle.close()
    if args.output:
        print(f"exported {count} facts to {args.output}")
    return 0


def cmd_import(store, config, args) -> int:
    deployment = args.deployment or config.deployment_id

    # Group by engineer so put_facts' provenance arguments reproduce each
    # fact's original author rather than flattening them onto one identity.
    by_engineer: dict[str, list[StoredFact]] = {}
    with open(args.input, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                fact = _fact_from_json(line)
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                print(
                    f"{args.input}:{line_number}: skipping malformed row ({exc})", file=sys.stderr
                )
                continue
            by_engineer.setdefault(fact.engineer_id, []).append(fact)

    accepted = rejected = 0
    reasons: dict[str, int] = {}
    for engineer_id, facts in by_engineer.items():
        result = store.put_facts(deployment_id=deployment, engineer_id=engineer_id, facts=facts)
        accepted += result.accepted
        rejected += result.rejected
        for reason, count in result.reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count

    print(f"imported {accepted} facts into deployment {deployment}")
    if rejected:
        detail = ", ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))
        print(f"rejected {rejected} ({detail})", file=sys.stderr)
    return 0


def cmd_stats(store, config, args) -> int:
    deployment = args.deployment or config.deployment_id
    health = store.health()
    print(f"store      : {health.backend} ({'ok' if health.ok else 'DEGRADED'})")
    print(f"detail     : {health.detail}")
    print(f"deployment : {deployment}")
    print(f"facts      : {store.count_facts(deployment_id=deployment)}")
    print(f"keys       : {len(store.list_api_keys(deployment_id=deployment))}")
    return 0


def _fact_from_json(line: str) -> StoredFact:
    raw = json.loads(line)
    known = {f.name for f in dataclasses.fields(StoredFact)}
    return StoredFact(**{k: v for k, v in raw.items() if k in known})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agora-admin",
        description="Operate a MemAgora deployment. Configured entirely by AGORA_* env vars.",
    )
    sub = parser.add_subparsers(dest="command")

    def add(name, handler, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=handler)
        p.add_argument(
            "--deployment",
            help="Override AGORA_DEPLOYMENT_ID for this command",
        )
        return p

    add("migrate", cmd_migrate, "Apply pending schema migrations")

    p_issue = add("issue-key", cmd_issue_key, "Mint an API key for an engineer")
    p_issue.add_argument("--engineer", required=True, help="Engineer identity recorded on facts")

    p_revoke = add("revoke-key", cmd_revoke_key, "Revoke an API key by its id")
    p_revoke.add_argument("key_id", help="Public key id (ak_…)")

    add("list-keys", cmd_list_keys, "List keys issued for a deployment")

    p_export = add("export", cmd_export, "Stream a deployment's facts as JSONL")
    p_export.add_argument("--output", help="File to write (default: stdout)")

    p_import = add("import", cmd_import, "Load facts from a JSONL export")
    p_import.add_argument("input", help="JSONL file produced by `agora-admin export`")

    add("stats", cmd_stats, "Show store health and fact counts")
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2

    config = load_config()
    try:
        store = build_store(config)
    except AgoraStoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        return args.handler(store, config, args)
    except AgoraStoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
