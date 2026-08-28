"""Transport-level tests for the palace→agora client.

Drives the real ``urllib`` code path against a stdlib ``ThreadingHTTPServer``
on loopback — no mocking of urllib internals, no FastAPI, no new dependency,
so this runs on every CI leg including the ones that do not install the server.

The fake agora records what it received, which is how the request shape (path,
method, Authorization header, JSON body) gets asserted rather than assumed.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from contracts import SCHEMA_VERSION, FactPayload
from mempalace.client import get_facts, post_facts


class Recorder:
    """What the fake server should do, and what it saw."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = {"accepted": 0, "rejected": 0, "message": None}
        self.fail_times = 0  # respond 503 this many times before succeeding
        self.raw_body = None  # override the JSON body with raw bytes
        self.delay = 0.0


@pytest.fixture
def agora():
    recorder = Recorder()

    class Handler(BaseHTTPRequestHandler):
        def _record(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            recorder.requests.append(
                {
                    "method": method,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(raw) if raw else None,
                }
            )

        def _respond(self):
            if recorder.delay:
                threading.Event().wait(recorder.delay)
            if recorder.fail_times > 0:
                recorder.fail_times -= 1
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":"storage_unavailable","message":"try again"}')
                return
            payload = (
                recorder.raw_body
                if recorder.raw_body is not None
                else json.dumps(recorder.body).encode()
            )
            self.send_response(recorder.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            self._record("POST")
            self._respond()

        def do_GET(self):
            self._record("GET")
            self._respond()

        def log_message(self, *args):
            pass  # keep pytest output clean

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    recorder.endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    yield recorder
    server.shutdown()
    server.server_close()


def a_fact(subject="api") -> FactPayload:
    return FactPayload(
        subject=subject,
        predicate="owned_by",
        object="platform",
        confidence=0.9,
        source_session_id="sess-1",
    )


# ── POST /facts ─────────────────────────────────────────────────────────


def test_post_sends_the_wire_format_to_the_facts_path(agora):
    agora.body = {"accepted": 1, "rejected": 0, "message": None}
    post_facts([a_fact()], endpoint=agora.endpoint, api_key="ak_1.secret")

    sent = agora.requests[0]
    assert sent["method"] == "POST"
    assert sent["path"] == "/facts"
    assert sent["headers"]["Authorization"] == "Bearer ak_1.secret"
    assert sent["headers"]["Content-Type"] == "application/json"

    assert sent["body"]["schema_version"] == SCHEMA_VERSION
    assert sent["body"]["facts"] == [
        {
            "subject": "api",
            "predicate": "owned_by",
            "object": "platform",
            "valid_from": None,
            "valid_to": None,
            "confidence": 0.9,
            "source_session_id": "sess-1",
            "decision_id": None,
            "schema_version": SCHEMA_VERSION,
        }
    ]


def test_trailing_slash_on_the_endpoint_does_not_double_up(agora):
    post_facts([a_fact()], endpoint=agora.endpoint + "/")
    assert agora.requests[0]["path"] == "/facts"


def test_no_authorization_header_when_no_key_is_configured(agora):
    post_facts([a_fact()], endpoint=agora.endpoint)
    assert "Authorization" not in agora.requests[0]["headers"]


def test_server_counts_are_reported_verbatim(agora):
    agora.body = {"accepted": 2, "rejected": 3, "message": "rejected — empty_subject: 3"}
    result = post_facts([a_fact(), a_fact("web")], endpoint=agora.endpoint)
    assert (result.accepted, result.rejected) == (2, 3)
    assert result.message == "rejected — empty_subject: 3"


def test_a_4xx_is_not_retried(agora):
    agora.status = 400
    agora.raw_body = b'{"error":"schema_version_unsupported","message":"upgrade the server"}'
    result = post_facts([a_fact()], endpoint=agora.endpoint)

    assert len(agora.requests) == 1  # no retry: it would fail identically
    assert result.rejected == 1
    assert "HTTP 400" in result.message
    assert "upgrade the server" in result.message


def test_a_5xx_is_retried_once_then_reported(agora):
    agora.fail_times = 5  # more failures than attempts
    result = post_facts([a_fact()], endpoint=agora.endpoint)

    assert len(agora.requests) == 2  # original + one retry
    assert result.rejected == 1
    assert "HTTP 503" in result.message


def test_a_transient_5xx_succeeds_on_the_retry(agora):
    agora.fail_times = 1
    agora.body = {"accepted": 1, "rejected": 0, "message": None}
    result = post_facts([a_fact()], endpoint=agora.endpoint)

    assert len(agora.requests) == 2
    assert result.accepted == 1


def test_a_malformed_body_is_not_retried(agora):
    agora.raw_body = b"<html>proxy error</html>"
    result = post_facts([a_fact()], endpoint=agora.endpoint)

    assert len(agora.requests) == 1
    assert result.rejected == 1
    assert "malformed response" in result.message


def test_a_timeout_reports_rejection_without_raising(agora):
    agora.delay = 1.0
    result = post_facts([a_fact()], endpoint=agora.endpoint, timeout=0.05)
    assert result.accepted == 0
    assert result.rejected == 1


def test_unicode_facts_survive_the_round_trip(agora):
    fact = FactPayload(subject="café-service", predicate="维护者", object="команда-π")
    post_facts([fact], endpoint=agora.endpoint)
    assert agora.requests[0]["body"]["facts"][0]["subject"] == "café-service"


# ── GET /facts ──────────────────────────────────────────────────────────


def test_get_facts_parses_the_response_into_contract_objects(agora):
    agora.body = {
        "facts": [
            {
                "subject": "api",
                "predicate": "owned_by",
                "object": "platform",
                "valid_from": "2026-01-01",
                "valid_to": None,
                "confidence": 0.9,
                "source_session_id": "sess-1",
                "schema_version": SCHEMA_VERSION,
                # Additive server-side fields the wire contract does not define:
                "fact_id": "f_abc",
                "engineer_id": "alice",
                "recorded_at": "2026-05-01T00:00:00+00:00",
                "current": True,
            }
        ],
        "next_cursor": "opaque",
    }
    response = get_facts(endpoint=agora.endpoint, api_key="ak_1.secret", limit=10)

    assert response.next_cursor == "opaque"
    assert response.facts == [
        FactPayload(
            subject="api",
            predicate="owned_by",
            object="platform",
            valid_from="2026-01-01",
            confidence=0.9,
            source_session_id="sess-1",
        )
    ]

    sent = agora.requests[0]
    assert sent["method"] == "GET"
    assert urlparse(sent["path"]).path == "/facts"
    assert parse_qs(urlparse(sent["path"]).query)["limit"] == ["10"]


def test_get_facts_passes_a_cursor_through(agora):
    agora.body = {"facts": [], "next_cursor": None}
    get_facts(endpoint=agora.endpoint, cursor="page-2")
    assert parse_qs(urlparse(agora.requests[0]["path"]).query)["cursor"] == ["page-2"]


def test_get_facts_skips_unparseable_rows_rather_than_failing(agora):
    agora.body = {
        "facts": [
            {"subject": "good", "predicate": "p", "object": "o"},
            {"predicate": "missing-a-subject"},
        ],
        "next_cursor": None,
    }
    response = get_facts(endpoint=agora.endpoint)
    assert [f.subject for f in response.facts] == ["good"]


def test_get_facts_returns_none_on_an_error_status(agora):
    agora.status = 401
    agora.raw_body = b'{"error":"unauthorized","message":"valid API key required"}'
    assert get_facts(endpoint=agora.endpoint) is None
