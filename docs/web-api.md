# Local web game operations

The Flask application is a local, single-process game host. It shares games
between local clients and serializes each request through validation, execution
and response serialization. This is not a public remote service or a
multi-process persistence/session API.

## Observation and progression

`GET /games/<id>` and `GET /games/` observe existing games. They do not call
`refresh`, enforce expired clocks, make decisions, or consume game RNG. Clock
fields such as `seconds_left` describe elapsed wall time, so these derived JSON
values can change between reads without changing a clock's stored state.

`POST /games/<id>/update` explicitly checks clocks and advances automatic
interactive steps. It also starts a game when the coach assigned START_GAME is a
bot. It accepts an empty body or `{}`. The browser uses this operation for
opponent polling and clock expiry; initial reads and recovery after a rejected
action remain GETs.

`POST /games/<id>/act` accepts `{"action": {...}}`. Actions use the existing
`action_type`, optional `player_id`, and optional integer `position.x` and
`position.y` fields. `null` and `CONTINUE` are subject to the core validator and
cannot skip a pending decision. A rejected action does not refresh the game or
change the previously accepted action, clocks, reports, trajectory, or RNG.

Creation (`PUT /game/create`) and team lookup accept the same game modes:
`standard`, `7v7`, `5v5`, `3v3`, `1v1`. Mode comparison is case-insensitive;
creation defaults to `standard`. Team names must match the selected mode's
rosters. Coaches are `human` or a registered bot; unknown coaches are invalid.

## Errors

Every HTTP failure has JSON shape
`{"error": {"code": "...", "message": "..."}}`. Invalid JSON, fields, mode,
name, or pagination return 400; missing resources return 404; initially rejected
client actions and duplicate/ambiguous save names return 409. Bot or engine
failures after client-action acceptance or during explicit update return 500
with `internal_error`, even if the internal failure is an invalid action.
Execution may already have advanced the game and RNG; this response does not
claim rejection or rollback of the client's action. GET observes the current
state without advancing it. Other unexpected engine failures also return 500,
and unreadable/corrupt local storage returns 500 with
`storage_error`. Failures never return a successful game response. Invalid
HTTP methods return 405 with the same JSON envelope.

## Local saves

`POST /game/save` takes `game_id` and `name`. Names contain 3–39 ASCII letters,
digits, spaces, underscores or hyphens, start with a letter or digit, and do not
end in a space. Names are case-insensitive. Invalid characters are rejected,
never stripped. Existing names, including case variants, cannot be overwritten.
Ambiguous legacy files differing only in case cause a conflict rather than an
arbitrary choice.

A save serializes a copy of the game into a temporary file in the save directory,
flushes/fsyncs it, and atomically links the complete file into place without
replacing an existing save. The temporary file is removed. Filesystem and
serialization failures do not change the live game. Filesystems must support
same-directory hard links; unsupported writes fail instead of falling back to
a partially visible write. This guarantees complete-file visibility, not
power-loss durability of the directory entry.

`POST /game/load/<name>` (with or without a trailing slash) restores a saved
game under a new game ID and preserves its RNG stream, Gaussian cache and
queued dice. It accepts only an empty body or `{}`. **Loading is now POST**;
the old GET load route returns 405 so observations cannot create games.
Restore never reseeds and offers no reseed parameter. Deliberate reseeding is a
separate core operation, `Game.set_seed`.

Clocks that ran when saved resume from their saved elapsed time; clocks already
paused for another decision stay paused. A legacy saved Game without this
clock marker uses the old resume-all-paused-clocks behavior. Listing saves does
not resume them. Delete operations remain `DELETE /game/<id>/delete` and
`DELETE /save/<name>/delete`; missing targets return 404.

The existing `.bb` and `.rep` files remain Python pickles for **trusted local
files only**. Their directories are local operator configuration and must not
be writable by untrusted parties. File names are checked before filesystem
access and symlinks/nonregular files are rejected on load. These checks do not
make pickle safe: unpickling can execute Python code. There is no upload or
client-supplied file-path operation, no new general snapshot format, and no
promise that files from incompatible engine versions can be restored. In
particular, saves missing the game-owned RNG are rejected instead of silently
inventing/reseeding state. The in-memory checkpoint API has no new persistence
guarantee.

## Replay pages

`GET /replays/<id>` returns the existing replay JSON envelope with at most the
first 100 frames. `GET /steps/<id>/<offset>/<count>` loads its replay as needed,
including when no replay metadata was requested first. Offset is a zero-based
**frame offset**, not a recording ID: action records can leave gaps between
frame IDs. Frames are ordered by increasing recording ID and keep those IDs as
response keys. Offset must be nonnegative and count must be 1–100. Empty
replays and offsets at or beyond the end return `{}` for a page.

Replay IDs are safe local basenames (1–200 ASCII letters, digits, spaces,
underscores, hyphens or dots, starting with a letter/digit, without `..` or a
trailing space/dot). Names generated by the existing recorder remain usable.
Each host retains at most eight whole replays by default, evicts the least
recently used entry, and reloads evicted files on demand. This is an entry
count limit, not a per-file size limit; replay files remain trusted local input.
Reads do not mutate cached frames, and replacement/deletion of a local file
invalidates its cached version. Eviction never deletes persisted replay files.

The browser source and shipped concatenated bundle use the same endpoints.
The interaction regression harness runs with
`gjs tests/web/frontend_contract.js` from the repository root. Flask tests use
isolated temporary host directories and need no listening network server.

## Local practice pause and browser feedback

Creation accepts the optional boolean `game.local` (default `false`). Selecting
**Local practice** in the browser sets it to `true`, using the existing
noncompetitive engine mode: elapsed clocks are displayed but do not force
player decisions on timeout. Existing competition presets retain their behavior.

Live game responses include `competition_mode`, `pause_allowed`, and `paused`.
`POST /games/<id>/pause` and `POST /games/<id>/resume` accept an empty body or
`{}` and return the observed game. Both operations are idempotent. Competition
games reject either operation with 409 `pause_not_allowed`; ended/closed games
return 409 `game_ended`. This is a local session control, not a game action or
an interruption of an already executing request. The host lock orders it with
other requests.

While paused, `/update` only observes and `/act` rejects with 409 `game_paused`
before engine validation/execution. No automatic driver steps, RNG consumption,
or clock charging occur. Resume restarts only clocks that ran at pause time;
a primary clock suspended for a secondary decision stays suspended. Elapsed
time uses #16's injectable monotonic source, and browser interpolation uses the
returned duration rather than audit wall timestamps. A save made during a
session pause loads paused and requires an explicit resume; the existing trusted
local save format and routes are unchanged.

The browser announces new live `TOUCHDOWN` reports in a polite text status for
four seconds, without taking focus or disabling decisions. Repeated observations
of the same cumulative report history do not restart the notice. Initial page
loads and replay navigation show historical events only in the game log, so
refresh/seek cannot reannounce old touchdowns. The notice never changes score.
No audio/artwork is required; reduced-motion preferences disable game CSS motion.

Player attributes are text in a labelled definition list, with flexible columns
and space separate from the sprite. Player squares retain pointer selection and
support Enter/Space selection with full attribute labels and selected state.

The additional browser checks run with `pytest tests/web/test_browser.py` after
installing the optional test tool `playwright==1.57.0` and running
`python -m playwright install chromium`. They exercise the shipped Angular bundle
and DOM against a temporary listening Flask server, suppress artwork requests,
and control the injected clock in the test process. No test endpoints or browser
tooling are added to the runtime. These are extra web checks beyond ordinary
lint and Python/native core CI. `tests/framework/test_web_pause.py` covers the
same pause boundary with the Flask client, including saved paused clocks.
