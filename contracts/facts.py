"""Wire schemas for the units of knowledge crossing palace→agora.

Two shapes travel this boundary:

* :class:`FactPayload` — an atomic temporal triple (subject, predicate,
  object) plus provenance. What the team's knowledge graph is made of.
* :class:`DecisionRecord` — the reasoning that produced a set of facts:
  what was chosen, why, what was rejected, what is still open. Facts say
  *what is true*; a decision says *why*, which is the question a teammate's
  agent actually asks six months later.

Raw conversation text never appears in either shape. A ``DecisionRecord``
carries prose the agent deliberately authored — a rationale it wrote, not a
transcript it copied — and the server caps every text field so the boundary
is enforced rather than merely intended.
"""

from dataclasses import dataclass, field
from typing import Optional


SCHEMA_VERSION = "0.3.0"


@dataclass(frozen=True)
class FactPayload:
    """A single classified fact propagating from a palace to the agora.

    Temporal triples follow the SCD Type 2 pattern from
    ``docs/schema.sql`` — ``valid_from`` and ``valid_to`` bound when the
    fact held, independent of when it was recorded.

    Fields:
        subject:   The entity the fact is about.
        predicate: The relationship type. Convention is snake_case.
        object:    The other side of the relationship. Free-form string;
                   may be a literal (e.g., a URL, a date) or another entity.
        valid_from:    ISO 8601 date this fact began holding (optional).
        valid_to:      ISO 8601 date this fact stopped holding (optional;
                       absent means still valid).
        confidence:    Classifier confidence in [0.0, 1.0]. Servers MAY
                       use this for ranking or for filtering low-confidence
                       facts out of query responses.
        source_session_id: Engineer's local session ID this fact came
                           from. Useful for audit, never for re-derivation
                           of raw content.
        decision_id:   Links this fact to the ``DecisionRecord`` that
                       produced it (added in 0.2.0). Absent for facts that
                       record a state of the world rather than a choice.
        schema_version:    Wire format version. Defaults to module
                           SCHEMA_VERSION; kept on each payload so a
                           server receiving older clients can migrate.
    """

    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    confidence: float = 1.0
    source_session_id: Optional[str] = None
    decision_id: Optional[str] = None
    schema_version: str = field(default=SCHEMA_VERSION)


@dataclass(frozen=True)
class DecisionRecord:
    """The reasoning behind a set of facts.

    Emitted by an agent calling ``memagora_record_decision`` at the moment a
    choice is made, while the alternatives and constraints are still in
    context. Reconstructing this after the fact — from a transcript, by a
    second LLM pass — is exactly what MemAgora decided not to do.

    ``decision_id`` is chosen by the emitting agent and shared with every
    ``FactPayload`` the decision produced, which is what lets a later session
    ask "why is it this way" and get the argument rather than the conclusion.

    Fields:
        decision_id: Stable id linking this record to its facts. Unique
                     within a deployment.
        title:       One line naming the decision.
        chosen:      The approach that was taken.
        rationale:   Why it was taken. Agent-authored prose, not transcript.
        alternatives_rejected: Options considered and set aside. Each entry
                     should say what it was and why it lost.
        constraints: What bounded the choice — deadlines, dependencies,
                     compatibility requirements, team conventions.
        open_questions: What the decision deliberately left unresolved.
        decided_on:  ISO 8601 date the decision was made (optional; the
                     server always stamps its own ingest time separately).
        source_session_id: Engineer's local session ID, for audit only.
        schema_version: Wire format version, as on ``FactPayload``.
    """

    decision_id: str
    title: str
    chosen: str
    rationale: str
    alternatives_rejected: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    decided_on: Optional[str] = None
    source_session_id: Optional[str] = None
    schema_version: str = field(default=SCHEMA_VERSION)


@dataclass(frozen=True)
class FactClose:
    """A request to end a fact that no longer holds.

    Facts are never deleted. Closing one sets ``valid_to``, so "we used SQS
    FIFO until September" stays answerable — which is the whole point of
    storing validity bounds rather than overwriting rows.

    The triple is named in full because that is what a client knows: fact ids
    are server-generated and no palace has ever seen one.

    Fields:
        subject / predicate / object: the open fact to close.
        valid_to: ISO 8601 date it stopped holding. Defaults, server-side, to
                  today — matching ``KnowledgeGraph.invalidate``.
    """

    subject: str
    predicate: str
    object: str
    valid_to: Optional[str] = None
    schema_version: str = field(default=SCHEMA_VERSION)
