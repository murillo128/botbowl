# HTTP/JSON and Python SDK

Install `botbowl[web]` for the optional Flask server. The synchronous SDK in
`botbowl.lab.http_client` uses only the Python standard library. Importing the
SDK does not import Flask or change the offline dataset reader. Existing web
routes and their legacy local saves are unchanged.

Trusted setup constructs a `SessionRegistry` and
`CommandGateway(registry, authenticate, creators=...)` as described in
[the command contract](commands.md). `authenticate` verifies a bearer credential
and returns a stable principal ID. `creators` maps each authorized creator's
principal to an explicit dictionary of per-session `Access` grants, including
the creator. Creation does not confer any role or capability automatically.
Only the operator supplies credentials and grants; neither is accepted in JSON.
Configure an evaluator with `close` for SDK-owned session context managers.

Register `create_blueprint(gateway, HTTPConfig())` in a Flask application, or use
`create_app(gateway, config)`. `serve(gateway)` provides a blocking local server
on `127.0.0.1:5000`, with debug/reloader disabled and registry cleanup on exit.
`registry.close()` is also available to the embedding application's shutdown
hook. Request teardown releases admission slots, not persistent sessions.

## Version 1 routes

All routes require `Authorization: Bearer <user-configured credential>`.
POST bodies are JSON. Responses preserve detached gateway JSON dictionaries;
no Game objects, pickle, paths or executable scenario inputs cross the boundary.

| Method and path under `/api/v1` | Request / response |
| --- | --- |
| `GET /capabilities` | Version, retry/resource bounds, exact catalog recipe versions |
| `POST /sessions` | Creation Command; returns a cached allocation receipt |
| `GET /sessions/<id>` | Coherent observation, legal actions, revision, cursor and driver pause state |
| `POST /commands` | Existing Command, including `step`, `close`, `restore`, `pause`, `resume`, `reset` |
| `GET /sessions/<id>/snapshot` | Authorized session snapshot; scenario sessions include their existing spec/session envelope |
| `GET /sessions/<id>/events?cursor=...&limit=128&wait=0` | Bounded gateway notifications after the cursor |

Every mutation carries the existing `session_id`, integer `request_id`,
`expected_revision` and `payload` Command fields. Creation uses
`session_id="new"`, `expected_revision=0` and `payload.op="create"`, with either:

* `config`: all `SessionConfig` fields, plus `seed`: all `SeedSpec.to_json()` fields.
  V1 exposes the packaged human-versus-human default profile: `game_config=null`,
  `home_team=away_team="human"`, size 1/3/5/7/11, decision budget 0–10,000 and
  step budget 1–100,000. Other profiles/loaders are unsupported.
* `scenario`: the complete `ScenarioSpecV1.to_json()` object. The existing
  [versioned catalog](scenarios.md) validates and constructs it. No recipes are
  reproduced in the transport. Unknown recipe/version/profile/operation returns
  `UnsupportedCapability`. Scenario reset is unsupported; create another episode.

Creation IDs increase per creator within the gateway lifetime, separately from
per-session command IDs. Creation receipts and high-water marks enforce the same
bounded idempotency semantics as ordinary commands. A failed accepted creation
also consumes its request ID. Session counts, pending calls, retained receipts,
JSON bytes/depth and snapshot codec limits remain gateway-owned.

Evaluator grants individually enable `pause` and `resume`. Players and spectators
cannot pause; competition sessions reject pause regardless of grant. Pause blocks
the explicit session driver and freezes only previously running local clocks.
Resume restores only those clocks, preserving primary/secondary clock behavior.
A changed pause state increases the session control revision. Snapshots retain
logical state independent of driver pause; capture normalizes clocks only in a
detached copy. Restore increases revision and releases the current driver pause.
Reads do not execute decisions or advance randomness. The server has no implicit
policy runner, GUI, clock refresh loop or background game progression.

Events are the public invalidation notifications from #59, not engine replay or
privileged labels. Pages advance their cursor only through returned events.
An overwritten/foreign/future cursor returns 410; read the session again to obtain
a coherent state and fresh cursor. Wait is bounded (default 5 seconds, configurable
up to 30), with polling outside the session lock. Each waiter occupies one bounded
HTTP admission slot. Slow consumers own no server queue. WSGI/proxy timeouts must
also bound slow socket reads/writes; the application bounds materialized JSON.

## Errors and deployment configuration

Error bodies contain `ok=false` and a stable `error` code. Accepted command
failures preserve their receipt fields. HTTP statuses are 400 invalid schema or
unsupported capability, 401 unauthenticated, 403 forbidden, 404 missing session,
409 revision/request conflict or closed session, 410 expired retry/cursor,
413 oversized request, 429 rate/admission/session/response capacity, and 500
sanitized internal failure. No exception text or credential is returned.

`HTTPConfig` defaults to loopback, no CORS, bounded per-principal request rates
and bounded concurrent requests. All Origin-bearing requests are rejected.
Responses use `Cache-Control: no-store`. Authentication, admission and byte limits
precede decoding or privileged materialization. Remote bind configuration requires
explicit authentication plus TLS or exact trusted proxy IPs. Direct TLS requests
must arrive as HTTPS; `serve(..., ssl_context=...)` requires a real configured
context when `tls=True`. A proxy must terminate TLS, replace
`X-Forwarded-Proto` with the single value `https`, and connect from a configured
IP. No forwarded host, client address or identity headers are trusted. Untrusted
peers cannot attest TLS. Debug-enabled parent applications are rejected.

For remote use, configure a production WSGI server/proxy with transport timeouts,
TLS and one registry-owning process. Do not fork live sessions or route one
session across independent registries. Restart invalidates session generations
and receipts. There is no persistent exactly-once promise across restart or
receipt expiry. No external service is deployed by this module.

## SDK and recovery

`HTTPClient(url, token, connect_timeout=5, read_timeout=10, retries=2)` is a
single-threaded context manager. Credentials come from the caller; redirects,
proxy discovery and plaintext remote connections are not supported. Each request
closes its socket on success or failure. `create(request_id=..., config=..., seed=...)`
or `create(request_id=..., scenario=...)` returns an owned `RemoteSession`.
`attach(id)` returns an unowned player/spectator handle. Closing created handles
closes the server session through the gateway; closing attached handles only
releases the handle. Close is idempotent after success; failed remote cleanup
raises, and does not mask an exception already leaving a context. A `create()`
call retains ownership intent before sending: successful `retry_pending()`
recovery registers the allocated session for cleanup even when `create()` never
returned a handle. Client close first attempts bounded recovery of a pending
owned creation within its original retry window, then closes recovered sessions
before discarding credentials. This also applies during exception unwinding.
When pending command recovery returns an authoritative failed receipt, owned
session cleanup continues with current state and the next request ID. Explicit
close reports the recovered error after cleanup; context unwinding preserves
the original exception. An unresolved pending command still blocks new writes.
Low-level `execute()` creation remains caller-managed.

`read`, `snapshot`, `events` and `execute(Command_dict)` expose the wire contract.
Handles provide `step(action, expected_revision)` and
`command(payload, expected_revision)`. Handle IDs increase locally; independently
created handles sharing a principal/session must coordinate explicit `request_id`
values. Event wait must be shorter than the read timeout.

Before the first write the client reads server retry bounds. Transport failures
retry the identical serialized Command, at most the configured number of times
and only within that window. Retry-cache eviction may still return 410 sooner.
A timeout/disconnect after send raises `UncertainResult` if recovery fails.
The pending command blocks new writes: call `retry_pending()` to recover its
receipt, or `read()` to inspect current state. After a lost/ambiguous response,
a guard rejection such as 429 or 410 cannot resolve the original outcome: the
client retains the same identity, body and deadline and raises `UncertainResult`.
Only a complete receipt matching the pending command resolves it (including a
cached failure receipt). A first-send guard rejection without a prior ambiguous
response remains an ordinary `HTTPError`. Never submit the uncertain play as
a new request ID. After expiry, no further send occurs; a 410 or expired local
window cannot establish whether the original command committed. Reconcile state
explicitly before continuing with a new client. In particular, an expired lost
creation response may require operator cleanup of the bounded orphan session.
Cleanup also reports failure when the server remains unavailable through its
bounded recovery attempts; it does not claim to release an unrecoverable session.

`examples/lab/http_match.py` controls both teams using only these public APIs,
without a GUI, and closes the owned session even on exception. Supply
`BOTBOWL_HTTP_URL` and user-configured `BOTBOWL_HTTP_TOKEN_ADMIN`,
`BOTBOWL_HTTP_TOKEN_HOME`, `BOTBOWL_HTTP_TOKEN_AWAY`. It can run from another
working directory against an installed wheel. Its 24-decision budget is an
administrative episode ending, not a manufactured sporting result.
