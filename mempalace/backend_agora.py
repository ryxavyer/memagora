"""MemAgora backend wrapper — transparent passthrough over ChromaBackend
with classifier hook + audit log.

Architecture:

    BaseBackend / BaseCollection (mempalace/backends/base.py)
            ▲
            │ implements
            │
        AgoraBackend / AgoraCollection (this module)
            │
            │ wraps
            ▼
        ChromaBackend / ChromaCollection (mempalace/backends/chroma.py)

Behavior:

* When ``AgoraConfig.enabled`` is ``False`` (no endpoint configured),
  the wrapper is a pure passthrough. Every method delegates to the
  inner ChromaCollection unchanged. Audit log is not touched.
* When ``enabled`` is ``True`` (endpoint configured), every ``add`` /
  ``upsert`` writes one audit entry per document recording what would
  have been classified. v0.1's classifier is a stub returning ``[]``,
  so no facts actually leave the machine — the wrapper still proves
  the seam works end-to-end.

The classifier and HTTP client wiring (calling ``classify()`` then
``post_facts()`` and recording the response) lands in v0.2. v0.1
records intent only, in dry-run mode by default.

Selection: registered as the ``agora`` entry-point in pyproject.toml.
Engineers opt in by setting ``MEMPALACE_BACKEND=agora`` (resolved by
``mempalace/backends/registry.py``); the default remains ``chroma``.
"""

from typing import Optional

from .audit import write_audit_entry
from .backends.base import (
    BaseBackend,
    BaseCollection,
    GetResult,
    HealthStatus,
    PalaceRef,
    QueryResult,
)
from .backends.chroma import ChromaBackend
from .config_agora import AgoraConfig, load_agora_config


class AgoraCollection(BaseCollection):
    """Per-collection wrapper. Delegates every read to the inner
    ChromaCollection; intercepts writes to record audit entries when
    enabled."""

    def __init__(self, inner: BaseCollection, config: AgoraConfig):
        self._inner = inner
        self._config = config

    # ── Writes (intercepted when enabled) ───────────────────────────────

    def add(
        self,
        *,
        documents,
        ids,
        metadatas=None,
        embeddings=None,
    ) -> None:
        self._inner.add(
            documents=documents, ids=ids, metadatas=metadatas, embeddings=embeddings
        )
        self._maybe_audit("add", ids)

    def upsert(
        self,
        *,
        documents,
        ids,
        metadatas=None,
        embeddings=None,
    ) -> None:
        self._inner.upsert(
            documents=documents, ids=ids, metadatas=metadatas, embeddings=embeddings
        )
        self._maybe_audit("upsert", ids)

    def update(
        self,
        *,
        ids,
        documents=None,
        metadatas=None,
        embeddings=None,
    ) -> None:
        self._inner.update(
            ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
        )
        # update is a metadata-only mutation in many cases; no audit
        # entry — v0.2 may revisit if classifier behavior dictates.

    def delete(self, *, ids=None, where=None) -> None:
        self._inner.delete(ids=ids, where=where)

    # ── Reads (pure passthrough) ────────────────────────────────────────

    def query(self, **kwargs) -> QueryResult:
        return self._inner.query(**kwargs)

    def get(self, **kwargs) -> GetResult:
        return self._inner.get(**kwargs)

    def count(self) -> int:
        return self._inner.count()

    def estimated_count(self) -> int:
        return self._inner.estimated_count()

    def close(self) -> None:
        return self._inner.close()

    def health(self) -> HealthStatus:
        return self._inner.health()

    # ── Audit hook ──────────────────────────────────────────────────────

    def _maybe_audit(self, op: str, ids: list[str]) -> None:
        """Record one audit entry per write when an endpoint is configured.

        v0.1: writes a stub entry indicating what the classifier *would
        have* been called on. v0.2 replaces ``would_classify=True`` with
        actual classification output and adds an entry per emitted
        FactPayload.
        """
        if not self._config.enabled:
            return
        for doc_id in ids:
            write_audit_entry(
                {
                    "op": op,
                    "id": doc_id,
                    "would_classify": True,
                    "dry_run": self._config.dry_run,
                }
            )


class AgoraBackend(BaseBackend):
    """BaseBackend implementation that wraps ChromaBackend.

    Construction is lightweight: no I/O, no network. Per-palace handles
    are created lazily by ChromaBackend on the first ``get_collection``
    call.
    """

    name = "agora"
    spec_version = "1.0"
    # Inherit ChromaBackend's capabilities — we don't change the
    # storage-side contract, only intercept on the way through.
    capabilities = ChromaBackend.capabilities

    def __init__(self) -> None:
        self._inner = ChromaBackend()
        self._config = load_agora_config()

    def get_collection(self, *args, **kwargs) -> BaseCollection:
        """Delegate to ChromaBackend.get_collection, then wrap.

        Accepts both the new kwargs-only shape (``palace=PalaceRef,
        collection_name=...``) and the legacy positional shape
        (``palace_path, collection_name, create=...``) — ChromaBackend
        normalizes both, so the wrapper just passes through.
        """
        inner_coll = self._inner.get_collection(*args, **kwargs)
        return AgoraCollection(inner_coll, self._config)

    def close_palace(self, palace: PalaceRef) -> None:
        self._inner.close_palace(palace)

    def close(self) -> None:
        self._inner.close()

    def health(self, palace: Optional[PalaceRef] = None) -> HealthStatus:
        return self._inner.health(palace)

    @classmethod
    def detect(cls, path: str) -> bool:
        # Explicit opt-in only. Auto-detection would interfere with
        # ChromaBackend.detect() and silently flip engineers into
        # MemAgora mode without an endpoint configured.
        return False
