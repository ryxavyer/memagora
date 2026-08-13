# Deploying an agora

One team, one deployment, one database. This document covers standing up a
MemAgora server for a single team, pointing engineers at it, operating it, and
moving it to a different storage backend later.

The engineer-side install needs none of this — a palace with no configured
endpoint behaves exactly like MemPalace alone.

## The isolation model

Two mechanisms, deliberately overlapping:

1. **One deployment per stack.** Each team runs its own server and its own
   database. Nothing is shared between deployments — not the process, not the
   volume, not the credentials.
2. **`deployment_id` on every row.** Each API key belongs to exactly one
   deployment, and the server derives `deployment_id` from the key on every
   request. Queries are scoped in the storage layer, below HTTP, so an endpoint
   cannot forget to filter.

The first is the deployment guidance. The second means that even a
misconfiguration that pointed two teams at one database could not let one team
read the other's facts.

## Standing one up

```bash
git clone <this repo> && cd memagora/agora
cp .env.example .env          # set AGORA_DEPLOYMENT_ID and POSTGRES_PASSWORD
docker compose up -d
docker compose exec agora agora-admin migrate
```

`migrate` is a separate, deliberate step. `AGORA_AUTO_MIGRATE` exists but
defaults to off: a restart should never silently reshape a team's database.

Check it:

```bash
curl -s localhost:8000/health
# {"status":"ok","version":"0.3.0","schema_versions":["0.x"]}
```

### Without Docker

The server is a plain ASGI app:

```bash
pip install ./contracts ./agora
export AGORA_STORE=postgres AGORA_DSN=postgresql://agora:secret@localhost/agora
export AGORA_DEPLOYMENT_ID=team-alpha
agora-admin migrate
agora-server
```

For a small team that does not want Postgres at all, `AGORA_STORE=sqlite` with
`AGORA_SQLITE_PATH` on a persistent volume passes the same conformance suite.
It serializes writes through one connection — fine for a handful of engineers,
not for a large one.

### TLS

API keys are bearer tokens. Put a TLS terminator (nginx, Caddy, your cloud load
balancer) in front of the server before it is reachable on a network; the
compose file binds to loopback by default to make that an explicit decision.

## Issuing engineer keys

```bash
docker compose exec agora agora-admin issue-key --engineer alice
```

```
key id     : ak_1a2b3c4d
engineer   : alice
deployment : team-alpha

Give this to the engineer — it is not recoverable:
ak_1a2b3c4d.9f8e7d6c5b4a39281706f5e4d3c2b1a0
```

The database stores the id and a SHA-256 digest of the secret. Nothing can
recover the secret afterwards, including you — reissue instead.

The `engineer` value is stamped on every fact that key writes. Use whatever
identity your team already recognizes (username, email local part).

Revoking is immediate:

```bash
docker compose exec agora agora-admin revoke-key ak_1a2b3c4d
docker compose exec agora agora-admin list-keys
```

## Pointing an engineer at it

On the engineer's machine, in `~/.mempalace/config.json`:

```json
{
  "agora": {
    "endpoint": "https://agora.example.com",
    "api_key": "ak_1a2b3c4d.9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    "dry_run": true
  }
}
```

or as environment variables:

```bash
export MEMPALACE_AGORA_ENDPOINT=https://agora.example.com
export MEMPALACE_AGORA_API_KEY=ak_1a2b3c4d.9f8e...
```

**Leave `dry_run` on first.** With it enabled the classifier runs and every
fact it would send is written to the local audit log, but no network call is
made. The engineer can read exactly what would cross before anything does:

```bash
mempalace audit tail -n 20
```

When they are satisfied, `MEMPALACE_AGORA_DRY_RUN=0` (or `"dry_run": false`)
turns on the POST. Each batch adds one more audit entry recording the endpoint
and the server's accepted/rejected counts — never the API key.

```bash
mempalace audit diff        # what the local log says vs. what the agora holds
```

## Operating it

```bash
docker compose exec agora agora-admin stats
docker compose logs -f agora
```

**Backups.** Everything is in Postgres; back it up however your team backs up
Postgres. `pg_dump` of the single database is sufficient — the server holds no
other state.

```bash
docker compose exec db pg_dump -U agora agora > agora-$(date +%F).sql
```

**Upgrades.**

```bash
git pull
docker compose build agora
docker compose up -d agora
docker compose exec agora agora-admin migrate
```

Migrations are numbered `.sql` files under
`agora/storage/migrations/<backend>/`, applied in filename order and recorded
in `schema_migrations`. Re-running `migrate` is always safe.

**Client/server skew.** Engineers upgrade on their own schedule. A client one
release ahead of the server still works: same-major payloads are accepted and
unknown fields ignored. A client whose major version is *newer* than the
server's is refused with `schema_version_unsupported` — upgrade the deployment.
`GET /health` reports what the server accepts.

## Changing storage backends

The storage layer is an interface (`agora/storage/base.py`), not an assumption
about Postgres. Postgres is the reference implementation; SQLite ships
alongside it; a team can install a third-party backend that registers itself
under the `agora.stores` entry-point group, or write one.

A new backend is credible when it passes the same conformance suite the
in-tree ones do:

```python
from agora.storage.testing import AbstractStoreContractSuite

class TestMyStore(AbstractStoreContractSuite):
    @pytest.fixture
    def store(self):
        store = MyStore(dsn=os.environ["MY_DSN"])
        store.migrate()
        yield store
        store.close()
```

Moving the data is an export and an import:

```bash
# 1. Freeze writes (stop the server, or revoke keys temporarily).
docker compose stop agora

# 2. Dump from the old backend.
AGORA_STORE=postgres AGORA_DSN=... \
    agora-admin export --output facts.jsonl

# 3. Prepare and load the new one.
AGORA_STORE=mystore MYSTORE_DSN=... agora-admin migrate
AGORA_STORE=mystore MYSTORE_DSN=... agora-admin import facts.jsonl

# 4. Repoint the server and restart.
```

Facts keep their ids, ingest timestamps, validity bounds, and per-engineer
provenance — `import` is a restore, not a re-ingest. It reports anything it
rejects (a duplicate open triple, a malformed row) on stderr rather than
failing the whole load.

API keys do **not** move. Reissue them on the new backend; that is deliberate,
since a backend swap is a good moment to drop keys nobody uses.

## Schema notes

The fact table descends from the palace-side knowledge graph's temporal triple
(`mempalace/knowledge_graph.py`; note that `docs/schema.sql` has drifted from
that code and is not the authority), extended with `deployment_id` and the
provenance columns `engineer_id`, `source_session_id`, `schema_version`, and
`recorded_at`. The palace's `source_closet` / `source_file` columns are
intentionally absent: they name storage local to one engineer's machine and
have no meaning to the team.

Temporal semantics match the palace exactly — `valid_from` / `valid_to` are
ISO-8601 text compared lexicographically, NULL means unbounded, and
`valid_to IS NULL` means the fact currently holds. A partial unique index
enforces at most one open row per `(deployment_id, subject, predicate, object)`,
so two engineers recording the same decision produce one row, not two.

The authoritative DDL is `agora/storage/migrations/postgres/001_init.sql`.
