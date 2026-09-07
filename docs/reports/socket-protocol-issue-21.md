# Legacy local socket transport validation

Scope: issue #21, based on accepted snapshot
`23d8c09a5822157a510174c9f0c6bfd3501f74f0`. The transport retains its ten-byte
ASCII decimal/padded length and Request/Response pickle envelopes. Wire payloads
are limited to 64 MiB by default. A configurable finite deadline defaults to 30
seconds; client exchanges share it across connect/send/receive and are capped by
the game clock. Server request and response share a deadline too.

## Acceptance coverage

`tests/ai/test_socket_protocol.py` exercises harmless local peers only:

| Contract | Evidence |
| --- | --- |
| Exact framing | Bytewise header and fragmented payload; two concatenated frames decoded separately; premature EOF at every offset of a complete frame |
| Bounded parsing | Invalid/zero/oversized lengths and configurations; exact configured limit; oversized header rejected before payload read/unpickling; malformed pickle |
| Total deadline | Header/payload trickling, stalled sender, zero timeout, client connect/send/receive sharing one budget, game-clock cap, server callback consuming its response budget |
| Public validation | Invalid envelopes, commands, game/team/result types, token/request-ID mismatches; separate `python -O` interpreter checks |
| Trust boundary | Client/server reject non-loopback activation before creating a socket; raw helpers reject non-loopback TCP peers; Unix socket pairs are accepted; logs omit test tokens |
| Resource lifecycle | 30 successful game exchanges interleaved with 30 malformed frames; 30 refused connections and 30 serialization failures; descriptor count and socket registry return to baseline in a fresh process with a 30-second bound; close before start/twice, bind failure, partial-frame shutdown and accept/close race |
| Docker lifecycle | Mocked API, remote-daemon, pull, missing-image, run and readiness failures; bounded readiness timeout; explicit cleanup and retry after failed kill; host networking without published bridge ports |
| Competition | Factory probe/matchup/factory-failure cleanup; two complete games through loopback socket agents in a spawned process with a 40-second join and bounded termination fallback |

## Reproduction

An isolated Python 3.11.16 environment at `/tmp/botbowl-issue21-venv` uses NumPy
1.24.3, Cython 3.3.0, pytest 9.1.1, Docker client 7.2.0, Gym 0.26.2 and Flask
3.1.3. Build/install followed the repository path in the issue worktree:

```sh
BOTBOWL_BUILD_NATIVE=1 python setup.py build
BOTBOWL_BUILD_NATIVE=1 uv pip install --no-build-isolation -e '.[dev,competition,rl,web]'
python -m pytest -q tests/ai/test_socket_protocol.py tests/ai/test_competition.py --require-pathfinding=native
timeout 900 python -m pytest -q --require-pathfinding=native
```

The extension is owned by this worktree. Other issue environments and loaded
extensions were not modified. Full logs and installed-version evidence remain
outside Git under `/tmp/botbowl-issue21-*`.

## Retained validation findings

The focused native check passed 228 tests. The first full run, before the final
regressions/test-fixture corrections, recorded 1,238 passed, one inherited xfail,
and two failures in the new deadline/resource tests. Its log is
`/tmp/botbowl-issue21-native.log`; no existing test or skip was changed.

A suite-prefix diagnostic identified retained Game graphs in mock call history
and a 1.81-second generation-2 garbage-collection pause during unpickling. The
server then correctly exhausted its two-second deadline. The resource test now
uses a counting agent without retaining call arguments, in a fresh bounded
process. The shared client deadline test checks exact remaining budgets with a
controlled monotonic clock, since socket deadlines cannot preempt GC or
serialization. Real socket timeout/trickle tests remain in place. Diagnostic
logs are `/tmp/botbowl-issue21-resource-suite-investigation.log` and
`/tmp/botbowl-issue21-deadline-investigation.log`.

A deterministic mock exposed an accept/close race: a server closed just as
accept returned could start reading the new peer. The final regression requires
that peer to be closed without reading it. Failed Docker kill handles are also
retained for explicit cleanup retry. Final validation is against these changes.

The separate known #20 controls still reproduce setup undo divergence at seed 3,
step 167, and `compare_iterable` KeyError on differing dictionary keys on both
the exact accepted-base archive and this source. The undo JSON is identical.
These controls do not explain or excuse the two new test failures above.
Their logs are `/tmp/botbowl-issue21-{base,head}-{undo,compare}-repro.log`.

## Boundaries and compatibility

The frame limit bounds serialized bytes, not decoded memory or CPU. Socket
budgets cannot preempt pickle code or bot callbacks. All peers and Docker images
must already be trusted; loopback is an exposure restriction, and post-unpickle
token checks provide no authentication against malicious pickle. No public
service or real Docker daemon was used in validation.

DockerAgent requires a local Unix-socket Docker daemon with host networking
support, normally Linux. The trusted image must bind loopback and honor
`BOTBOWL_SOCKET_PORT`. The previous bridge-port publication path is removed.
Host networking is not a sandbox; remote untrusted use needs the replacement
protocol tracked by #2 API-08/09. See `docs/docker.md` for lifecycle usage.

Direct Competition callers retain ownership of supplied agents; factory-created
MultiAgentCompetition agents are closed explicitly. The source changes do not
alter RNG/procedure/dice, CI, packaging or inherited forward-model helpers.
