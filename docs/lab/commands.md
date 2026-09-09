# Session command gateway

`botbowl.lab.commands` is an in-process command boundary for a future transport.
It opens no ports and does not change the trusted legacy TCP/pickle protocol.

Trusted application setup constructs a `SessionRegistry`, registers sessions with
`SessionConfig`, `SeedSpec`, and per-principal `Access` grants, then constructs a
`CommandGateway(registry, authenticate)`. The authentication callable verifies
transport credentials and returns a stable principal ID or `None`. It must not
trust a principal/role claimed in request data. Configure credentials externally;
there are no built-in accounts or tokens. Session IDs are public identifiers.
Authentication runs before request decoding or any session/privileged-state read.

```python
from botbowl.lab.commands import Access, CommandGateway, SessionRegistry
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig

registry = SessionRegistry()
session_id = registry.register(
    SessionConfig(size=1), SeedSpec(17),
    {"coach-a": Access("player", "home"),
     "coach-b": Access("player", "away"),
     "audience": Access("spectator")},
)
# authenticate is supplied by the trusted application.
gateway = CommandGateway(registry, authenticate)
```

Only the registry owns the constructed sessions. Do not access its private
sessions, share them with another writer, fork a live registry into another
process, or use mutable authentication policy as a request-controlled grant.
Construct a new registry on restart. A fresh registry UUID and never-reused
registration counter make old session generations invalid, including when the
same game/seed is loaded again. No durable exactly-once guarantee is made.

## Commands and retries

`execute(credential, raw)` accepts UTF-8 JSON **bytes**, with exactly these fields:

```json
{
  "session_id": "<returned session identifier>",
  "request_id": 1,
  "expected_revision": 1,
  "payload": {
    "op": "step",
    "action": {
      "schema_version": 1,
      "type": "START_GAME",
      "actor_id": "away",
      "player_id": null,
      "target_id": null,
      "position": null,
      "options": {}
    }
  }
}
```

Use an action from `read(...)["legal_actions"]`. Other command payloads are
`{"op": "reset"}`, `{"op": "close"}`, and
`{"op": "restore", "snapshot": <exported session snapshot>}`. Reset uses only
the original trusted configuration and seed. No request can choose local paths,
modules, callables, roles, or arbitrary configuration loaders.

`request_id` is an integer from 1 through 2^63-1, strictly increasing for each
principal/session's **new** commands. Coordinate allocation and ordering across
clients using the same principal. The session lock covers retry lookup, revision
comparison, application, receipt storage, and event publication. Known identical
retries are returned before checking the current revision or current actor.
Request identity is the SHA-256 digest of the canonical complete command,
including the expected revision; JSON whitespace/key order does not matter.
Changing a cached command under the same ID raises `Conflict`.

Receipts expire `retry_seconds` after completion (default 300). At most
`max_retries` (default 64) receipts survive per session, across all principals;
insertion of more receipts evicts the oldest. Retries do not extend the window.
Each principal retains a high-water request ID for the life of the session.
An ID at or below that mark whose receipt is no longer present raises
`ExpiredRequest`. This includes a previously skipped ID or out-of-order new
request. It cannot silently execute after cache eviction or expiration.

Accepted envelopes return a detached receipt containing `ok`, a sanitized
`error` code or null, `session_id`, `request_id`, `state_revision`, and `cursor`.
Revision conflicts and failed applications also retain receipts. Malformed input,
authentication/access failures, request-ID conflicts/expiration, and admission
failure raise typed `GatewayError` subclasses. A failed engine operation that
advances the session revision still publishes a change notification and a cached
failure. Unexpected exceptions close the session and log only a fixed error code.

Close releases the engine exactly once and rejects further new writes. Closed
sessions retain receipts in a separate bounded retired table (at most
`max_sessions` entries). An identical close retry returns its original receipt
while retained. Retired-table eviction invalidates that session generation; later
requests raise `InvalidGeneration`. Both tables, and all retry state, are local to
one registry lifetime.

## Authorization and snapshots

| Grant | Read state/legal actions/events | Step | Privileged operations |
| --- | --- | --- | --- |
| Player | Yes, configured observer side | Only configured side, while it is the actor | None |
| Spectator | Yes, public view | No | None |
| Evaluator | Yes, public view | No | Only explicitly configured capabilities |

Evaluator capability names are `labels`, `snapshot`, `restore`, `reset`, and
`close`. They are immutable trusted grants on each individual session. A role
never grants these implicitly. Labels are supplied through trusted registration
and never included in state, command receipts, or reconnect events.

`read(credential, session_id, resource="snapshot")` exports a session snapshot:
facade budget metadata plus an `engine` string containing a `SnapshotFileV1`
document. Restore accepts exactly this envelope. Engine decoding exclusively uses
`read_snapshot` from the bounded snapshot codec, with configured `SnapshotLimits`,
after checking the restore capability. Server-owned temporary directories are
cleaned up on success and failure; client input never supplies a path. No custom
adapters or pickle fallback are installed. Invalid/incompatible imports leave the
live state, RNG and revision unchanged.

Trusted code can register an independent branch using
`registry.register(config, seed, grants, branch=session_snapshot)`. Branch grants
are explicit and independent of the source grants. A remote principal can access
only branch session IDs granted to it. Remote requests cannot allocate branches
or grant themselves access; branch creation and labels belong to trusted setup.

## Reconnection and resource limits

`read(credential, session_id)` returns detached state, legal actions, revision,
and a cursor from the same session lock. It is also the resynchronization
operation after `EventGap`. `read(..., cursor=previous_cursor)` returns ordered
change notifications after that cursor and a new cursor. Each notification
contains a gateway sequence, monotonic session revision, operation, and success
flag. These are public invalidation notifications, not a replay of engine events
or an evaluation/privileged channel. Reset/restore cannot rewind this cursor.

The event deque retains at most `max_events` notifications (default 128).
Overwritten, foreign, malformed, or future cursors raise `EventGap`; losses are
never represented as an empty successful read. Consumers retain only detached
copies and have no registered queues. A slow consumer cannot hold the session
lock while transmitting or accumulate server buffers. The gateway performs no
network writes or consumer callbacks inside locks.

`GatewayLimits` bounds live sessions (32), retired sessions (32), principals per
session (64), admitted concurrent operations including reads (32), request bytes
(4 MiB), JSON nesting (32), retry receipts, event count, and read-response bytes
(4 MiB). Full admission rejects immediately with `CapacityExceeded`. A request
waiting for a session lock consumes one admission slot. JSON depth is checked
before recursive parsing; duplicate keys and non-finite numbers are rejected.
Command receipts and event records have a fixed small schema; snapshot work is
additionally bounded by `SnapshotLimits`. Trusted session/engine configuration
and engine trajectory retention have their own existing session limits; this
gateway does not replace those with a streaming engine implementation.
