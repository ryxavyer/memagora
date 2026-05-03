# memagora-contracts

Wire format shared between MemAgora palace clients (engineer-side) and
the agora server (team-side).

Pure dataclass schemas. No runtime dependencies. Independently
versioned so palace and agora can release on different cadences.

## Why a separate package

Rolling deploys of a self-hosted agora mean engineer client v1.2 may be
talking to server v1.3. Both sides need a stable, importable schema
that doesn't pull in the rest of either codebase. A future third-party
client (a different language SDK, a TUI, a webhook receiver) can
install just `memagora-contracts` without `mempalace` or the server.

## Contents

- `contracts.facts.FactPayload` — single classified fact crossing palace → agora
- `contracts.api.PostFactsRequest` / `PostFactsResponse` — POST /facts
- `contracts.api.GetFactsResponse` — GET /facts
- `contracts.SCHEMA_VERSION` — current wire format version (semver)

## Stability

Pre-v1.0: anything may change. The `schema_version` field is on every
payload so servers can refuse or migrate older clients.
