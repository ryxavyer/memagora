"""Fact payload schema — the unit of structured knowledge crossing palace→agora.

A FactPayload is a temporal triple (subject, predicate, object) plus
provenance. Engineers' classifiers emit these; the agora server stores
and serves them. Raw conversation text never appears in this shape.
"""

from dataclasses import dataclass, field
from typing import Optional


SCHEMA_VERSION = "0.1.0"


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
    schema_version: str = field(default=SCHEMA_VERSION)
