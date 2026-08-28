"""Pydantic models for the HTTP surface.

Two definitions of the same payload exist on purpose:

* ``contracts.FactPayload`` — stdlib dataclass, zero dependencies, Python 3.9.
  That is what engineer-side code and any third-party client installs.
* the models here — pydantic v2, used only inside the server for parsing and
  validation.

``tests/test_agora_models_parity.py`` asserts the field names match, so the two
cannot drift silently. ``extra="ignore"`` is what lets a newer client POST
fields this server has never heard of without being rejected.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from contracts import SCHEMA_VERSION, DecisionRecord, FactClose, FactPayload

from .storage.base import StoredDecision, StoredFact


class FactIn(BaseModel):
    """One fact as it arrives on the wire. Mirrors ``contracts.FactPayload``.

    ``schema_version`` is optional here even though the contract gives it a
    default: an omitted per-fact version falls back to the envelope's.
    """

    model_config = ConfigDict(extra="ignore")

    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    confidence: float = 1.0
    source_session_id: Optional[str] = None
    decision_id: Optional[str] = None
    schema_version: Optional[str] = None

    def to_contract(self, *, schema_version: Optional[str] = None) -> FactPayload:
        return FactPayload(
            subject=self.subject,
            predicate=self.predicate,
            object=self.object,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            confidence=self.confidence,
            source_session_id=self.source_session_id,
            decision_id=self.decision_id,
            schema_version=schema_version or self.schema_version or SCHEMA_VERSION,
        )


class PostFactsIn(BaseModel):
    """Body of ``POST /facts`` — mirrors ``contracts.PostFactsRequest``."""

    model_config = ConfigDict(extra="ignore")

    facts: list[FactIn]
    schema_version: str = SCHEMA_VERSION


class PostFactsOut(BaseModel):
    """Mirrors ``contracts.PostFactsResponse``."""

    accepted: int
    rejected: int
    message: Optional[str] = None


class FactOut(BaseModel):
    """A stored fact on the way out.

    The eight ``FactPayload`` keys plus four additive server-side fields.
    ``contracts/api.py`` already tells clients to tolerate unknown fields, so
    adding these does not break the 0.1.0 wire contract.
    """

    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    confidence: float = 1.0
    source_session_id: Optional[str] = None
    decision_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    # Additive, server-side:
    fact_id: str
    engineer_id: str
    recorded_at: str
    current: bool

    @classmethod
    def from_stored(cls, fact: StoredFact) -> "FactOut":
        return cls(
            subject=fact.subject,
            predicate=fact.predicate,
            object=fact.object,
            valid_from=fact.valid_from,
            valid_to=fact.valid_to,
            confidence=fact.confidence,
            source_session_id=fact.source_session_id,
            decision_id=fact.decision_id,
            schema_version=fact.schema_version,
            fact_id=fact.fact_id,
            engineer_id=fact.engineer_id,
            recorded_at=fact.recorded_at,
            current=fact.current,
        )


class DecisionIn(BaseModel):
    """One decision as it arrives on the wire. Mirrors ``contracts.DecisionRecord``."""

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    title: str
    chosen: str
    rationale: str
    alternatives_rejected: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decided_on: Optional[str] = None
    source_session_id: Optional[str] = None
    schema_version: Optional[str] = None

    def to_contract(self, *, schema_version: Optional[str] = None) -> DecisionRecord:
        return DecisionRecord(
            decision_id=self.decision_id,
            title=self.title,
            chosen=self.chosen,
            rationale=self.rationale,
            alternatives_rejected=list(self.alternatives_rejected),
            constraints=list(self.constraints),
            open_questions=list(self.open_questions),
            decided_on=self.decided_on,
            source_session_id=self.source_session_id,
            schema_version=schema_version or self.schema_version or SCHEMA_VERSION,
        )


class DecisionOut(BaseModel):
    """A stored decision on the way out: the wire record plus provenance."""

    decision_id: str
    title: str
    chosen: str
    rationale: str
    alternatives_rejected: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decided_on: Optional[str] = None
    source_session_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    # Additive, server-side:
    engineer_id: str
    recorded_at: str

    @classmethod
    def from_stored(cls, decision: StoredDecision) -> "DecisionOut":
        return cls(
            decision_id=decision.decision_id,
            title=decision.title,
            chosen=decision.chosen,
            rationale=decision.rationale,
            alternatives_rejected=list(decision.alternatives_rejected),
            constraints=list(decision.constraints),
            open_questions=list(decision.open_questions),
            decided_on=decision.decided_on,
            source_session_id=decision.source_session_id,
            schema_version=decision.schema_version,
            engineer_id=decision.engineer_id,
            recorded_at=decision.recorded_at,
        )


class FactCloseIn(BaseModel):
    """One fact to end. Mirrors ``contracts.FactClose``."""

    model_config = ConfigDict(extra="ignore")

    subject: str
    predicate: str
    object: str
    valid_to: Optional[str] = None
    schema_version: Optional[str] = None

    def to_contract(self, *, schema_version: Optional[str] = None) -> FactClose:
        return FactClose(
            subject=self.subject,
            predicate=self.predicate,
            object=self.object,
            valid_to=self.valid_to,
            schema_version=schema_version or self.schema_version or SCHEMA_VERSION,
        )


class IngestIn(BaseModel):
    """Body of ``POST /ingest`` — mirrors ``contracts.IngestRequest``."""

    model_config = ConfigDict(extra="ignore")

    facts: list[FactIn] = Field(default_factory=list)
    decisions: list[DecisionIn] = Field(default_factory=list)
    closes: list[FactCloseIn] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


class IngestOut(BaseModel):
    """Mirrors ``contracts.IngestResponse``."""

    facts_accepted: int
    facts_rejected: int
    decisions_accepted: int
    decisions_rejected: int
    facts_closed: int = 0
    message: Optional[str] = None


class GetDecisionsOut(BaseModel):
    """Mirrors ``contracts.GetDecisionsResponse``."""

    decisions: list[DecisionOut]
    next_cursor: Optional[str] = None


class GetFactsOut(BaseModel):
    """Mirrors ``contracts.GetFactsResponse``."""

    facts: list[FactOut]
    next_cursor: Optional[str] = None


class HealthOut(BaseModel):
    """``GET /health``. The unauthenticated form omits everything optional."""

    status: str
    version: str
    schema_versions: list[str]
    store: Optional[str] = None
    store_ok: Optional[bool] = None
    store_detail: Optional[str] = None
    deployment_id: Optional[str] = None
    fact_count: Optional[int] = None


class ErrorOut(BaseModel):
    """Every non-2xx response body. One shape, always."""

    error: str = Field(description="Stable machine-readable code")
    message: str
