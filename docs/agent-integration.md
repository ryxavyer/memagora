# Agent integration

How an agent puts knowledge into the agora and gets it back out. This is the
document to read before configuring a deployment's `CLAUDE.md`, and the one to
point a teammate at when they ask what the tools are for.

## The premise

MemAgora does not run its own LLM. The agent in the session already understood
the conversation — asking a second model to re-read the transcript and guess
what mattered would be both redundant and worse, because the second model was
not there when the decision was argued.

So the agent emits. It calls a tool at the moment a choice is made, while the
alternatives are still in context. Everything else in this document follows
from that.

## The tools

### Writing

**`memagora_record_decision`** — the one that matters. A decision is a title, a
chosen approach, a rationale, and — critically — the alternatives that lost and
why. Facts recorded alongside it are linked automatically.

```
memagora_record_decision(
  title="Queue for the notifications service",
  chosen="SQS FIFO",
  rationale="Per-recipient ordering is a hard requirement, and FIFO gives it
             without application-level sequencing.",
  alternatives_rejected=["Kafka — operational surface too large for one queue",
                         "SNS fan-out — no ordering guarantee"],
  constraints=["Must stay inside the existing AWS account"],
  open_questions=["Do we need a DLQ before launch?"],
  facts=[{"subject": "notifications-service", "predicate": "uses",
          "object": "SQS FIFO", "valid_from": "2026-08-01"}])
```

A decision without its alternatives is just an assertion. The teammate reading
this in six months does not need to be told what was chosen — they can see
that in the code. They need to know what was already tried.

**`memagora_record_fact`** — one triple, for things that are true without being
a decision: ownership, contracts, deprecation dates, conventions.

```
memagora_record_fact(subject="auth-service", predicate="owned_by",
                     object="platform team")
```

When the team changes its mind, pass `supersedes`:

```
memagora_record_fact(subject="notifications-service", predicate="uses",
                     object="Kinesis", supersedes="SQS FIFO",
                     valid_from="2026-09-01")
```

That closes the old fact and opens the new one in a single request. Without it
the agora holds both as current, and a teammate's agent has no way to tell
which answer is stale — the failure mode this whole system exists to prevent.

### Reading

**`memagora_why(subject, predicate)`** — the question worth asking before
changing something deliberate. Returns the current fact and the reasoning
behind it.

**`memagora_facts_about(subject)`** — what the team already knows about an
entity. Ask before assuming ownership or conventions; the answer may predate
this engineer.

**`memagora_decisions_about(subject)`** — the decisions behind those facts.

**`memagora_timeline(subject)`** — how the answer changed over time, and when.

## What belongs in the agora

The test is not "is this true" but **"would a teammate be wrong not to know
it?"**

| Emit | Do not emit |
|---|---|
| "We chose Postgres over DynamoDB because of the join in the reporting path" | "The test suite passes now" |
| "auth-service is owned by the platform team" | "I refactored `parse_config` into two functions" |
| "The v1 webhook API is deprecated on 2026-09-01" | "This bug was a typo in the env var name" |
| "Retries must be idempotent — the payment provider does not dedupe" | "Trying a different approach to the query" |
| "We deliberately do not cache this; staleness broke billing in March" | Anything specific to one engineer's machine |

Debugging, exploration, and things that will be untrue next week stay in the
palace. The palace is private and cheap to write to; the agora is shared and
expensive to get wrong.

## Prose, not transcript

`rationale`, `alternatives_rejected`, `constraints`, and `open_questions` are
free text, and they are the one place raw conversation could leak into a shared
store. They are for the argument **in the agent's own words** — never pasted
conversation, never quoted user messages, never code.

The server enforces the boundary rather than trusting it: 4000 characters per
prose field, 20 entries per list. A rationale that needs more than that is
probably a transcript.

## Dry run

`dry_run` defaults to **on**. With it on, every emission is written to the
engineer's local audit log and *nothing* is sent. The tool result says so
explicitly, and an agent must repeat that to the user rather than claiming the
team can see it.

```bash
mempalace audit tail -n 20     # exactly what would have crossed
```

When the engineer is satisfied, `MEMPALACE_AGORA_DRY_RUN=0` turns on the POST.

This is the trust boundary: an engineer gets to watch what their agent would
share before any of it leaves the machine.

## Encouraging emission in a deployment

Emission depends on the agent choosing to call the tool. A deployment that
wants its agora populated should say so in its own `CLAUDE.md`:

```markdown
## Team memory

When we make a decision with a rationale — a technology choice, an ownership
change, a deprecation, a constraint we discovered the hard way — call
`memagora_record_decision` before moving on. Include what we rejected and why.

Before changing something that looks deliberate, call `memagora_why` first.
```

Without a prompt like that, a session that made three architectural decisions
can end with an empty agora, and nothing in the system will notice.

## When the agora is unreachable

Emission tools report the failure and record it locally; they never raise, and
never block the session. The facts are in the audit log:

```bash
mempalace audit diff      # what the log holds that the agora does not
mempalace audit resend    # send exactly that, once the server is back
```
