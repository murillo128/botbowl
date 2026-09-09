"""Versioned executable replays over semantic actions and persistent snapshots.

ReplayV1 is deliberately separate from the legacy pickle-backed visual Replay.
Confirmed blocks are immutable JSON; checkpoints are SnapshotFileV1 documents.
"""

import hashlib
import json
import os
import pickle
import re
import tempfile
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass, fields
from functools import wraps
from pathlib import Path

from botbowl.core.game import Game
from botbowl.core.model import Replay as LegacyReplay

from .actions import ActionControl, ActionRequestV1, ActionV1
from .recording import _path, _read_file, _sync_directory
from .records import (
    MAX_RECORD_BYTES,
    EventV1,
    TransitionV1,
    decode_json,
    encode_json,
    identifier,
    safe_relative,
)
from .records import context as validate_context
from .rules import RulesDescriptor, describe_rules
from .snapshot_io import (
    SnapshotFileError,
    SnapshotIncompatibleError,
    SnapshotLimits,
    read_snapshot,
    snapshot_hash,
    write_snapshot,
)
from .snapshots import LogicalTime, capture_snapshot, clone_from_snapshot
from .timeline import Timeline

_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_FILE_HASH = re.compile(r"[0-9a-f]{64}")
_DEFAULT_LIMITS = SnapshotLimits()


class ReplayError(RuntimeError):
    """Base error for executable replay operations."""

    def __init__(self, message, *, decision_seq=None):
        self.decision_seq = decision_seq
        super().__init__(message)


class ReplayFormatError(ReplayError):
    """A replay is malformed, incomplete, or internally discontinuous."""

    code = "replay_invalid"


class ReplayIncompatibleError(ReplayFormatError):
    """The replay format or executable component versions are unsupported."""

    code = "replay_incompatible"


class ReplayDivergenceError(ReplayError):
    """Supplied actions no longer reproduce the recorded state."""

    code = "replay_diverged"

    def __init__(self, decision_seq, message="Replay diverged"):
        super().__init__(
            message + " at decision " + str(decision_seq), decision_seq=decision_seq
        )


def _read_boundary(index):
    """Normalize malformed data/I/O failures and retain the affected decision."""

    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            decision = index(self, *args, **kwargs)
            try:
                return method(self, *args, **kwargs)
            except ReplayError as error:
                if error.decision_seq is None:
                    error.decision_seq = decision
                    error.args = (str(error) + " at decision " + str(decision),)
                raise
            except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
                raise ReplayFormatError(
                    str(error) + " at decision " + str(decision),
                    decision_seq=decision,
                ) from error

        return guarded

    return decorate


class LegacyReplayPermissionError(ReplayError):
    """Legacy pickle data was not explicitly authorized as trusted."""

    code = "legacy_replay_untrusted"


def _require(condition, message, incompatible=False):
    if not condition:
        error = ReplayIncompatibleError if incompatible else ReplayFormatError
        raise error(message)


def _id(value):
    try:
        identifier(value)
    except ValueError as error:
        raise ReplayFormatError(str(error)) from error
    return value


def _json_file(path):
    payload = _read_file(path, MAX_RECORD_BYTES)
    _require(payload.endswith(b"\n"), "Partial replay JSON file")
    try:
        return decode_json(payload[:-1]), payload
    except ValueError as error:
        raise ReplayFormatError("Invalid replay JSON") from error


def _atomic_json(path, value):
    raw = encode_json(value) + b"\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix="." + path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return len(raw), hashlib.sha256(raw).hexdigest()


def _context(value):
    try:
        validate_context(value)
    except ValueError as error:
        raise ReplayFormatError(str(error)) from error
    return value


def _turn_index(decisions):
    result = []
    by_key = {}
    for row in decisions:
        after = row["after"]
        key = (
            after["branch_id"],
            after["half"],
            after["round"],
            after["team_turn_seq"],
        )
        entry = by_key.get(key)
        if entry is None:
            entry = {
                "branch_id": key[0],
                "half": key[1],
                "round": key[2],
                "team_turn_seq": key[3],
                "decision_start": row["after"]["decision_seq"],
                "decision_stop": row["after"]["decision_seq"] + 1,
                "event_start": row["event_start"],
                "event_stop": row["event_stop"],
            }
            by_key[key] = entry
            result.append(entry)
        else:
            entry["decision_stop"] = row["after"]["decision_seq"] + 1
            entry["event_start"] = min(entry["event_start"], row["event_start"])
            entry["event_stop"] = max(entry["event_stop"], row["event_stop"])
    return result


def _event_index(decisions):
    result = []
    for decision_offset, row in enumerate(decisions):
        decision = row["after"]["decision_seq"]
        for event_offset, event in enumerate(row["events"]):
            result.append(
                {
                    "event_seq": event["context"]["event_seq"],
                    "decision_seq": decision,
                    "decision_offset": decision_offset,
                    "event_offset": event_offset,
                    "previous_decision": decision - 1,
                    "next_decision": decision,
                }
            )
    return result


@dataclass(frozen=True)
class ReplayContinuation:
    """An independent resumable game and detached replay ancestry."""

    game: Game
    origin_family_id: str
    origin: dict


@dataclass(frozen=True)
class IndexedReplayEvent:
    """An event plus the executable boundary immediately before it."""

    event: dict
    context: dict
    previous_decision: int
    next_decision: int
    game: Game


@dataclass
class LegacyReplayView:
    """Trusted visual navigation only; it intentionally has no resume API."""

    replay: LegacyReplay

    @property
    def executable(self):
        return False

    def first(self):
        return self.replay.first()

    def last(self):
        return self.replay.last()

    def next(self):
        return self.replay.next()

    def prev(self):
        return self.replay.prev()


def open_legacy_replay(source, *, trusted=False):
    """Open old pickle data only after explicit trust, for visual use only."""
    if trusted is not True:
        raise LegacyReplayPermissionError(
            "Legacy replay pickle data requires trusted=True and is not executable"
        )
    if isinstance(source, LegacyReplay):
        replay = deepcopy(source)
    else:
        with open(source, "rb") as stream:
            replay = pickle.load(stream)
    if not isinstance(replay, LegacyReplay):
        raise ReplayFormatError("Expected a legacy Replay object")
    for step in replay.steps.values():
        if "state" in step.game:
            step.game["state"]["reports"] = [
                report.to_json() for report in replay.reports[: step.num_reports]
            ]
    return LegacyReplayView(replay)


class ReplayRecorder:
    """Record supplied semantic decisions into an atomic ReplayV1 directory.

    Construct before ``game.init()`` for a fresh game. A continuation returned
    by :meth:`ReplayReader.resume_at_decision` may also be supplied directly.
    Drive the game through :meth:`advance`; direct advances are detected as a
    discontinuity and are never silently incorporated.
    """

    def __init__(
        self,
        subject,
        destination,
        relative_path,
        *,
        replay_id,
        origin_family_id=None,
        checkpoint_interval=64,
        adapters=None,
        limits=_DEFAULT_LIMITS,
    ):
        continuation = subject if type(subject) is ReplayContinuation else None
        game = continuation.game if continuation is not None else subject
        _require(
            type(game) is Game and game.external_control,
            "Executable replay requires an externally controlled Game",
        )
        if game.time_source is None:
            _require(
                not game.state.clocks and game.start_time is None,
                "Attach replay logical time before the first game action",
            )
            game.time_source = LogicalTime()
        _require(
            type(game.time_source) is LogicalTime,
            "Executable replay requires LogicalTime",
        )
        _id(replay_id)
        if continuation is not None:
            if origin_family_id is None:
                origin_family_id = continuation.origin_family_id
            _require(
                origin_family_id == continuation.origin_family_id,
                "Derived replay must preserve origin_family_id",
            )
            origin = deepcopy(continuation.origin)
        else:
            _id(origin_family_id)
            origin = None
        _id(origin_family_id)
        _require(
            type(checkpoint_interval) is int and 1 <= checkpoint_interval <= 100000,
            "checkpoint_interval must be a positive bounded integer",
        )
        safe_relative(relative_path)

        if game.timeline is None:
            timeline = Timeline(game, episode_id=replay_id)
        else:
            _require(
                continuation is not None and type(game.timeline) is Timeline,
                "A fresh replay cannot adopt existing timeline ownership",
            )
            timeline = game.timeline
        _require(
            timeline._pending is None,
            "Replay must start at a settled decision boundary",
        )

        self.root = Path(destination).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.final = _path(self.root, relative_path)
        self.partial = _path(self.root, relative_path + ".partial")
        _require(
            not self.final.exists() and not self.partial.exists(),
            "Replay destination already exists",
        )
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.partial.mkdir()
        (self.partial / "blocks").mkdir()
        (self.partial / "checkpoints").mkdir()

        self.game = game
        self.timeline = timeline
        self.actions = ActionControl(game, timeline._entities)
        self.replay_id = replay_id
        self.origin_family_id = origin_family_id
        self.origin = origin
        self.checkpoint_interval = checkpoint_interval
        self.adapters = adapters
        self.limits = limits
        self.failed = False
        self.closed = False
        self.confirmed = False
        self._started = False
        self._start_context = None
        self._last_context = None
        self._last_hash = None
        self._descriptor = None
        self._pending_rows = []
        self._blocks = []
        self._checkpoints = []
        self._attachment_context = timeline.context.to_json()

    def _check(self):
        if self.failed or self.closed:
            raise ReplayError("Replay recorder is not open")

    def _snapshot(self):
        snapshot = capture_snapshot(self.game)
        return snapshot, snapshot_hash(
            snapshot, adapters=self.adapters, limits=self.limits
        )

    def _write_checkpoint(self, snapshot, semantic, context):
        decision = context["decision_seq"]
        relative = f"checkpoints/decision-{decision:020d}.snapshot.json"
        path = _path(self.partial, relative)
        envelope = write_snapshot(
            path,
            snapshot,
            adapters=self.adapters,
            limits=self.limits,
            provenance={
                "replay_id": self.replay_id,
                "origin_family_id": self.origin_family_id,
                "decision_seq": decision,
                "event_seq": context["event_seq"],
                "origin": self.origin,
                "descriptor": self._descriptor,
            },
        )
        raw = _read_file(path, self.limits.max_bytes)
        record = {
            "decision_seq": decision,
            "event_seq": context["event_seq"],
            "path": relative,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "semantic_state_hash": semantic,
            "payload_digest": envelope.payload_digest,
        }
        self._checkpoints.append(record)
        return record, envelope

    def _ensure_started(self):
        if self._started:
            return
        _require(
            self.timeline.context.to_json() == self._attachment_context,
            "Game advanced outside ReplayRecorder before its first append",
        )
        snapshot, semantic = self._snapshot()
        context = self.timeline.context.to_json()
        self._descriptor = describe_rules(
            self.game.config,
            self.game.ruleset,
            self.game.arena,
            self.game.state.home_team,
            self.game.state.away_team,
        ).to_json()
        _, envelope = self._write_checkpoint(snapshot, semantic, context)
        self._descriptor = envelope.descriptor
        self._start_context = deepcopy(context)
        self._last_context = deepcopy(context)
        self._last_hash = semantic
        self._started = True
        _sync_directory(self.partial / "checkpoints")

    def _boundary(self):
        current = self.timeline.context.to_json()
        _require(
            current == self._last_context and self.timeline._pending is None,
            "Game advanced outside ReplayRecorder or changed branch",
        )

    def advance(self, action, *, max_steps=100000):
        """Execute exactly one supplied action; policies are never consulted."""
        self._check()
        try:
            self._ensure_started()
            self._boundary()
            if type(action) is ActionRequestV1:
                request = action
                semantic = action.action
            elif type(action) is ActionV1:
                semantic = action
                request = self.actions.request(semantic)
            else:
                semantic = self.actions.encode(action)
                request = self.actions.request(semantic)
            core = self.actions.decode(request)
            _, before_hash = self._snapshot()
            _require(
                before_hash == self._last_hash,
                "Replay pre-state changed without a decision",
            )
            result = self.game.advance(core, max_steps=max_steps)
            _require(
                len(result.decisions) == 1 and result.decisions[0].status == "resolved",
                "Replay decisions must settle at an executable boundary",
            )
            envelope = result.decisions[0]
            _require(
                envelope.action == semantic,
                "Executed semantic action disagrees with recording",
            )
            expected = self._last_context["decision_seq"] + 1
            _require(
                envelope.after.decision_seq == expected
                and envelope.before.to_json() == self._last_context,
                "Decision sequence is discontinuous",
            )
            after_snapshot, after_hash = self._snapshot()
            row = envelope.to_json()
            row.update(schema_version=1, pre_hash=before_hash, post_hash=after_hash)
            self._pending_rows.append(row)
            self._last_context = envelope.after.to_json()
            self._last_hash = after_hash
            if (
                expected - self._start_context["decision_seq"]
            ) % self.checkpoint_interval == 0:
                self._seal_block(after_snapshot)
            return result
        except Exception:
            self.failed = True
            raise

    def append(self, action):
        """Recorder-compatible append of one supplied semantic decision."""
        self.advance(action)

    def flush(self):
        """Atomically confirm the current bounded block inside ``.partial``."""
        self._check()
        try:
            self._ensure_started()
            self._boundary()
            if self._pending_rows:
                snapshot, semantic = self._snapshot()
                _require(semantic == self._last_hash, "Replay flush state changed")
                self._seal_block(snapshot)
        except Exception:
            self.failed = True
            raise

    def _seal_block(self, end_snapshot):
        if not self._pending_rows:
            return
        rows = deepcopy(self._pending_rows)
        start = rows[0]["after"]["decision_seq"]
        stop = rows[-1]["after"]["decision_seq"] + 1
        event_start = rows[0]["before"]["event_seq"] + 1
        event_stop = rows[-1]["after"]["event_seq"] + 1
        checkpoint_decision = rows[0]["before"]["decision_seq"]
        _require(
            self._checkpoints[-1]["decision_seq"] == checkpoint_decision,
            "Replay block is missing its starting checkpoint",
        )
        self._write_checkpoint(end_snapshot, self._last_hash, self._last_context)
        ordinal = len(self._blocks)
        relative = f"blocks/block-{ordinal:08d}.json"
        document = {
            "format": "ReplayBlockV1",
            "version": 1,
            "replay_id": self.replay_id,
            "origin_family_id": self.origin_family_id,
            "checkpoint_decision": checkpoint_decision,
            "end_checkpoint": stop - 1,
            "decision_start": start,
            "decision_stop": stop,
            "event_start": event_start,
            "event_stop": event_stop,
            "decisions": rows,
            "event_index": _event_index(rows),
            "turn_index": _turn_index(rows),
        }
        size, digest = _atomic_json(_path(self.partial, relative), document)
        self._blocks.append(
            {
                "path": relative,
                "bytes": size,
                "sha256": digest,
                "checkpoint_decision": checkpoint_decision,
                "end_checkpoint": stop - 1,
                "decision_start": start,
                "decision_stop": stop,
                "event_start": event_start,
                "event_stop": event_stop,
                "turn_index": deepcopy(document["turn_index"]),
            }
        )
        self._pending_rows.clear()
        _sync_directory(self.partial / "blocks")

    def close(self, *, truncation_reason=None):
        """Confirm the replay atomically; nonterminal recordings name truncation."""
        self._check()
        try:
            self._ensure_started()
            self._boundary()
            terminal = self.game.state.game_over
            _require(
                (truncation_reason is None) == terminal,
                "Supply truncation_reason exactly for a nonterminal replay",
            )
            if truncation_reason is not None:
                _id(truncation_reason)
            snapshot, semantic = self._snapshot()
            _require(semantic == self._last_hash, "Replay final state changed")
            if self._pending_rows:
                self._seal_block(snapshot)
            end = (
                {"kind": "terminal", "reason": "game_over"}
                if terminal
                else {"kind": "truncated", "reason": truncation_reason}
            )
            indexes = {
                "decisions": [
                    {
                        "start": b["decision_start"],
                        "stop": b["decision_stop"],
                        "block": b["path"],
                    }
                    for b in self._blocks
                ],
                "events": [
                    {
                        "start": b["event_start"],
                        "stop": b["event_stop"],
                        "block": b["path"],
                    }
                    for b in self._blocks
                    if b["event_start"] != b["event_stop"]
                ],
                "turns": [
                    dict(deepcopy(turn), block=b["path"])
                    for b in self._blocks
                    for turn in b["turn_index"]
                ],
                "checkpoints": [c["decision_seq"] for c in self._checkpoints],
            }
            manifest = {
                "format": "ReplayV1",
                "version": 1,
                "replay_id": self.replay_id,
                "origin_family_id": self.origin_family_id,
                "origin": deepcopy(self.origin),
                "descriptor": deepcopy(self._descriptor),
                "checkpoint_interval": self.checkpoint_interval,
                "initial_context": deepcopy(self._start_context),
                "final_context": deepcopy(self._last_context),
                "end": end,
                "blocks": deepcopy(self._blocks),
                "checkpoints": deepcopy(self._checkpoints),
                "indexes": indexes,
            }
            _atomic_json(self.partial / "manifest.json", manifest)
            _sync_directory(self.partial)
            _require(
                not self.final.exists(),
                "Replay destination appeared before confirmation",
            )
            os.rename(self.partial, self.final)
            _sync_directory(self.final.parent)
            self.closed = self.confirmed = True
            return deepcopy(manifest)
        except Exception:
            self.failed = True
            raise

    def abort(self):
        """Stop recording and leave the explicit ``.partial`` evidence."""
        self.closed = True


class ReplayReader:
    """Bounded ReplayV1 reader; seeks load one checkpoint and needed blocks."""

    @_read_boundary(lambda self, *args, **kwargs: 0)
    def __init__(
        self, destination, relative_path, *, adapters=None, limits=_DEFAULT_LIMITS
    ):
        safe_relative(relative_path)
        self.root = Path(destination).resolve()
        self.directory = _path(self.root, relative_path)
        if (
            relative_path.endswith(".partial")
            or not (self.directory / "manifest.json").is_file()
        ):
            raise ReplayFormatError(
                "Executable replay has no confirmed manifest; legacy data needs open_legacy_replay"
            )
        self.adapters = adapters
        self.limits = limits
        self._manifest, _ = _json_file(_path(self.directory, "manifest.json"))
        self._validate_manifest()
        self.last_seek_replayed = 0
        self.last_seek_blocks = 0

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    @property
    def turn_index(self):
        return deepcopy(self._manifest["indexes"]["turns"])

    @_read_boundary(lambda self: 0)
    def _validate_manifest(self):
        data = self._manifest
        expected = {
            "format",
            "version",
            "replay_id",
            "origin_family_id",
            "origin",
            "descriptor",
            "checkpoint_interval",
            "initial_context",
            "final_context",
            "end",
            "blocks",
            "checkpoints",
            "indexes",
        }
        _require(
            type(data) is dict and set(data) == expected, "Invalid ReplayV1 manifest"
        )
        _require(
            data["format"] == "ReplayV1"
            and type(data["version"]) is int
            and data["version"] == 1,
            "Unsupported replay format/version",
            incompatible=True,
        )
        _id(data["replay_id"])
        _id(data["origin_family_id"])
        _require(
            type(data["checkpoint_interval"]) is int
            and 1 <= data["checkpoint_interval"] <= 100000,
            "Invalid checkpoint interval",
        )
        initial, final = (
            _context(data["initial_context"]),
            _context(data["final_context"]),
        )
        _require(
            initial["episode_id"] == final["episode_id"]
            and initial["decision_seq"] <= final["decision_seq"]
            and initial["event_seq"] <= final["event_seq"],
            "Invalid replay contexts",
        )
        descriptor_fields = {field.name for field in fields(RulesDescriptor)}
        _require(
            type(data["descriptor"]) is dict
            and set(data["descriptor"]) == descriptor_fields
            and all(type(v) is str for v in data["descriptor"].values()),
            "Invalid replay descriptor",
        )
        _require(type(data["origin"]) in (dict, type(None)), "Invalid replay origin")
        if data["origin"] is not None:
            origin = data["origin"]
            _require(
                set(origin) == {"replay_id", "decision_seq", "event_seq", "branch_id"},
                "Invalid replay origin",
            )
            _id(origin["replay_id"])
            _id(origin["branch_id"])
            _require(
                type(origin["decision_seq"]) is int
                and origin["decision_seq"] >= 0
                and type(origin["event_seq"]) is int
                and origin["event_seq"] >= 0,
                "Invalid replay origin context",
            )
        _require(
            type(data["end"]) is dict
            and set(data["end"]) == {"kind", "reason"}
            and data["end"]["kind"] in ("terminal", "truncated"),
            "Invalid replay end",
        )
        _id(data["end"]["reason"])
        _require(
            type(data["blocks"]) is list
            and type(data["checkpoints"]) is list
            and type(data["indexes"]) is dict
            and set(data["indexes"]) == {"decisions", "events", "turns", "checkpoints"},
            "Invalid replay indexes",
        )
        checkpoints = data["checkpoints"]
        _require(bool(checkpoints), "Replay requires an initial checkpoint")
        previous = None
        for checkpoint in checkpoints:
            _require(
                type(checkpoint) is dict
                and set(checkpoint)
                == {
                    "decision_seq",
                    "event_seq",
                    "path",
                    "bytes",
                    "sha256",
                    "semantic_state_hash",
                    "payload_digest",
                },
                "Invalid checkpoint index",
            )
            safe_relative(checkpoint["path"])
            _require(
                type(checkpoint["decision_seq"]) is int
                and type(checkpoint["event_seq"]) is int
                and type(checkpoint["bytes"]) is int
                and checkpoint["bytes"] > 0
                and type(checkpoint["sha256"]) is str
                and _FILE_HASH.fullmatch(checkpoint["sha256"])
                and type(checkpoint["semantic_state_hash"]) is str
                and _HASH.fullmatch(checkpoint["semantic_state_hash"])
                and type(checkpoint["payload_digest"]) is str
                and _HASH.fullmatch(checkpoint["payload_digest"]),
                "Invalid checkpoint metadata",
            )
            _require(
                previous is None or checkpoint["decision_seq"] > previous,
                "Checkpoint decisions are repeated or unordered",
            )
            previous = checkpoint["decision_seq"]
        _require(
            checkpoints[0]["decision_seq"] == initial["decision_seq"]
            and checkpoints[0]["event_seq"] == initial["event_seq"]
            and checkpoints[-1]["decision_seq"] == final["decision_seq"]
            and checkpoints[-1]["event_seq"] == final["event_seq"],
            "Checkpoint boundaries disagree with replay contexts",
        )

        next_decision = initial["decision_seq"] + 1
        next_event = initial["event_seq"] + 1
        for block in data["blocks"]:
            block_fields = {
                "path",
                "bytes",
                "sha256",
                "checkpoint_decision",
                "end_checkpoint",
                "decision_start",
                "decision_stop",
                "event_start",
                "event_stop",
                "turn_index",
            }
            _require(
                type(block) is dict and set(block) == block_fields,
                "Invalid block index",
            )
            safe_relative(block["path"])
            _require(
                type(block["bytes"]) is int
                and block["bytes"] > 0
                and type(block["sha256"]) is str
                and _FILE_HASH.fullmatch(block["sha256"]),
                "Invalid block file metadata",
            )
            _require(
                all(
                    type(block[name]) is int
                    for name in (
                        "checkpoint_decision",
                        "end_checkpoint",
                        "decision_start",
                        "decision_stop",
                        "event_start",
                        "event_stop",
                    )
                )
                and type(block["turn_index"]) is list
                and block["decision_start"] == next_decision
                and block["decision_stop"] > block["decision_start"]
                and block["decision_stop"] - block["decision_start"]
                <= data["checkpoint_interval"]
                and block["checkpoint_decision"] == block["decision_start"] - 1
                and block["end_checkpoint"] == block["decision_stop"] - 1,
                "Decision block gap, duplicate, or invalid checkpoint",
            )
            _require(
                block["event_start"] == next_event
                and block["event_stop"] >= block["event_start"],
                "Event block gap or duplicate",
            )
            next_decision = block["decision_stop"]
            next_event = block["event_stop"]
        _require(
            next_decision == final["decision_seq"] + 1
            and next_event == final["event_seq"] + 1,
            "Replay indexes do not reach final context",
        )
        _require(
            [item["decision_seq"] for item in checkpoints]
            == [initial["decision_seq"]]
            + [item["end_checkpoint"] for item in data["blocks"]]
            and [item["event_seq"] for item in checkpoints]
            == [initial["event_seq"]]
            + [item["event_stop"] - 1 for item in data["blocks"]],
            "Replay checkpoints do not match block boundaries",
        )
        expected_indexes = {
            "decisions": [
                {
                    "start": b["decision_start"],
                    "stop": b["decision_stop"],
                    "block": b["path"],
                }
                for b in data["blocks"]
            ],
            "events": [
                {"start": b["event_start"], "stop": b["event_stop"], "block": b["path"]}
                for b in data["blocks"]
                if b["event_start"] != b["event_stop"]
            ],
            "turns": [
                dict(deepcopy(turn), block=b["path"])
                for b in data["blocks"]
                for turn in b["turn_index"]
            ],
            "checkpoints": [c["decision_seq"] for c in checkpoints],
        }
        _require(
            data["indexes"] == expected_indexes, "Replay index aliases are inconsistent"
        )

    @_read_boundary(lambda self, metadata: metadata["decision_start"])
    def _block(self, metadata):
        path = _path(self.directory, metadata["path"])
        document, raw = _json_file(path)
        _require(
            len(raw) == metadata["bytes"]
            and hashlib.sha256(raw).hexdigest() == metadata["sha256"],
            "Replay block byte count/hash mismatch",
        )
        expected = {
            "format",
            "version",
            "replay_id",
            "origin_family_id",
            "checkpoint_decision",
            "end_checkpoint",
            "decision_start",
            "decision_stop",
            "event_start",
            "event_stop",
            "decisions",
            "event_index",
            "turn_index",
        }
        _require(
            type(document) is dict
            and set(document) == expected
            and document["format"] == "ReplayBlockV1"
            and type(document["version"]) is int
            and document["version"] == 1,
            "Invalid ReplayBlockV1",
        )
        for name in (
            "replay_id",
            "origin_family_id",
            "checkpoint_decision",
            "end_checkpoint",
            "decision_start",
            "decision_stop",
            "event_start",
            "event_stop",
            "turn_index",
        ):
            _require(
                document[name]
                == (self._manifest[name] if name in self._manifest else metadata[name]),
                "Replay block metadata mismatch",
            )
        rows = document["decisions"]
        _require(
            type(rows) is list,
            "Replay block decisions must be a list",
        )
        for offset, row in enumerate(rows):
            decision = metadata["decision_start"] + offset
            self._validate_row(row, decision)
            expected_before = rows[offset - 1]["after"] if offset else None
            if expected_before is not None and row["before"] != expected_before:
                raise ReplayFormatError(
                    "Decision contexts are discontinuous at decision " + str(decision),
                    decision_seq=decision,
                )
        if rows:
            _require(
                rows[0]["event_start"] == metadata["event_start"]
                and rows[-1]["event_stop"] == metadata["event_stop"],
                "Block events disagree with indexed boundaries",
            )
        _require(
            document["event_index"] == _event_index(rows)
            and document["turn_index"] == _turn_index(rows),
            "Replay block indexes are inconsistent",
        )
        if len(rows) != metadata["decision_stop"] - metadata["decision_start"]:
            affected = min(
                metadata["decision_start"] + len(rows), metadata["decision_stop"]
            )
            raise ReplayFormatError(
                "Replay block decision count mismatch at decision " + str(affected),
                decision_seq=affected,
            )
        self.last_seek_blocks += 1
        return document

    @_read_boundary(lambda self, row, decision: decision)
    def _validate_row(self, row, decision):
        expected_fields = {
            "schema_version",
            "before",
            "after",
            "actor_id",
            "team_id",
            "action",
            "event_start",
            "event_stop",
            "events",
            "next_actor_id",
            "terminal",
            "status",
            "macro_id",
            "primitive_order",
            "pre_hash",
            "post_hash",
        }
        _require(
            type(row) is dict and set(row) == expected_fields,
            "Unknown or missing decision fields",
        )
        _require(
            all(
                type(row[name]) is str and _HASH.fullmatch(row[name])
                for name in ("pre_hash", "post_hash")
            ),
            "Invalid replay state hash",
        )
        # Reuse DATA-02's accepted action/event domains. Observation references
        # are validation-only placeholders: replay does not store model inputs.
        before, after = row["before"], row["after"]
        transition = {
            name: row[name]
            for name in (
                "schema_version",
                "before",
                "after",
                "actor_id",
                "action",
                "event_start",
                "event_stop",
                "next_actor_id",
                "status",
                "macro_id",
                "primitive_order",
            )
        }
        transition.update(
            transition_id=[after["episode_id"], after["branch_id"], decision],
            source_family=self._manifest["origin_family_id"],
            scenario_id=self._manifest["replay_id"],
            pre_observation=1,
            post_observation=2,
            end={"kind": "terminal", "reason": "game_over"}
            if row["terminal"]
            else None,
        )
        TransitionV1(transition)
        _require(
            row["status"] == "resolved"
            and type(row["events"]) is list
            and len(row["events"]) == row["event_stop"] - row["event_start"],
            "Unresolved decision or invalid event interval",
        )
        _require(
            type(row["terminal"]) is bool
            and row["team_id"] == row["actor_id"]
            and row["macro_id"] is None
            and row["primitive_order"] is None,
            "Invalid executable decision metadata",
        )
        _require(
            before["episode_id"] == self._manifest["initial_context"]["episode_id"]
            and before["branch_id"] == self._manifest["initial_context"]["branch_id"],
            "Foreign replay decision",
        )
        for offset, event in enumerate(row["events"]):
            _require(
                type(event) is dict
                and set(event) == {"context", "decision_seq", "kind", "data"},
                "Unknown or missing event fields",
            )
            _require(
                event["context"]["event_seq"] == row["event_start"] + offset
                and event["decision_seq"] == decision,
                "Repeated, missing, or uncaused replay event",
            )
            EventV1(
                dict(
                    event,
                    schema_version=1,
                    payload_version=1,
                    event_id=[
                        event["context"]["episode_id"],
                        event["context"]["branch_id"],
                        event["context"]["event_seq"],
                    ],
                )
            )
            _require(
                event["context"]["episode_id"] == after["episode_id"]
                and event["context"]["branch_id"] == after["branch_id"],
                "Foreign replay event",
            )

    @_read_boundary(lambda self, decision: decision)
    def _checkpoint(self, decision):
        choices = [item["decision_seq"] for item in self._manifest["checkpoints"]]
        index = bisect_right(choices, decision) - 1
        _require(index >= 0, "Decision predates replay")
        metadata = self._manifest["checkpoints"][index]
        return self._load_checkpoint(metadata)

    @_read_boundary(lambda self, metadata: metadata["decision_seq"])
    def _load_checkpoint(self, metadata):
        path = _path(self.directory, metadata["path"])
        raw = _read_file(path, self.limits.max_bytes)
        _require(
            len(raw) == metadata["bytes"]
            and hashlib.sha256(raw).hexdigest() == metadata["sha256"],
            "Replay checkpoint byte count/hash mismatch",
        )
        try:
            snapshot = read_snapshot(path, adapters=self.adapters, limits=self.limits)
        except SnapshotIncompatibleError as error:
            raise ReplayIncompatibleError(
                "Replay checkpoint is incompatible"
            ) from error
        except SnapshotFileError as error:
            raise ReplayFormatError("Replay checkpoint is invalid") from error
        document = json.loads(raw)
        _require(
            document["scope"] == "engine"
            and document["payload_digest"] == metadata["payload_digest"],
            "Checkpoint scope or payload identity mismatch",
        )
        _require(
            document["provenance"]
            == {
                "replay_id": self._manifest["replay_id"],
                "origin_family_id": self._manifest["origin_family_id"],
                "decision_seq": metadata["decision_seq"],
                "event_seq": metadata["event_seq"],
                "origin": self._manifest["origin"],
                "descriptor": self._manifest["descriptor"],
            },
            "Checkpoint provenance differs from replay manifest",
        )
        semantic = snapshot_hash(snapshot, adapters=self.adapters, limits=self.limits)
        _require(
            semantic == metadata["semantic_state_hash"],
            "Replay checkpoint semantic hash mismatch",
        )
        game = clone_from_snapshot(snapshot, adapters=self.adapters)
        _require(
            game.timeline is not None
            and game.timeline.context.decision_seq == metadata["decision_seq"]
            and game.timeline.context.event_seq == metadata["event_seq"],
            "Replay checkpoint timeline mismatch",
        )
        expected_context = next(
            (
                self._manifest[name]
                for name in ("initial_context", "final_context")
                if self._manifest[name]["decision_seq"] == metadata["decision_seq"]
            ),
            None,
        )
        _require(
            expected_context is None
            or game.timeline.context.to_json() == expected_context,
            "Checkpoint boundary context mismatch",
        )
        if metadata["decision_seq"] == self._manifest["final_context"]["decision_seq"]:
            _require(
                game.state.game_over == (self._manifest["end"]["kind"] == "terminal"),
                "Replay end disagrees with final checkpoint",
            )
        return metadata, game, semantic

    def seek_decision(self, decision_seq):
        """Restore the nearest prior checkpoint and verify only the needed span."""
        return self._replay_span(decision_seq)

    def replay_all(self):
        """Verify every decision from the initial snapshot, including checkpoints."""
        return self._replay_span(
            self._manifest["final_context"]["decision_seq"], from_start=True
        )

    def _replay_span(self, decision_seq, *, from_start=False):
        initial = self._manifest["initial_context"]["decision_seq"]
        final = self._manifest["final_context"]["decision_seq"]
        _require(
            type(decision_seq) is int and initial <= decision_seq <= final,
            "Decision index is outside this replay",
        )
        self.last_seek_replayed = 0
        self.last_seek_blocks = 0
        checkpoint, game, current_hash = self._checkpoint(
            initial if from_start else decision_seq
        )
        if checkpoint["decision_seq"] == decision_seq:
            return game
        control = ActionControl(game, game.timeline._entities)
        for metadata in self._manifest["blocks"]:
            if metadata["decision_stop"] <= checkpoint["decision_seq"] + 1:
                continue
            if metadata["decision_start"] > decision_seq:
                break
            block = self._block(metadata)
            for row in block["decisions"]:
                decision = row["after"]["decision_seq"]
                if decision <= checkpoint["decision_seq"]:
                    continue
                if decision > decision_seq:
                    break
                try:
                    if current_hash != row["pre_hash"]:
                        raise ReplayDivergenceError(
                            decision, "Replay pre-state hash diverged"
                        )
                    semantic = ActionV1.from_json(row["action"])
                    core = control.decode(control.request(semantic))
                    result = game.advance(core)
                    if len(result.decisions) != 1 or result.decisions[0].to_json() != {
                        key: value
                        for key, value in row.items()
                        if key not in ("schema_version", "pre_hash", "post_hash")
                    }:
                        raise ReplayDivergenceError(
                            decision, "Replay events or logical context diverged"
                        )
                    current_hash = snapshot_hash(
                        capture_snapshot(game),
                        adapters=self.adapters,
                        limits=self.limits,
                    )
                    if current_hash != row["post_hash"]:
                        raise ReplayDivergenceError(
                            decision, "Replay post-state hash diverged"
                        )
                    self.last_seek_replayed += 1
                except ReplayDivergenceError:
                    raise
                except Exception as error:
                    raise ReplayDivergenceError(decision) from error
                if from_start and decision == metadata["end_checkpoint"]:
                    _, stored, stored_hash = self._checkpoint(decision)
                    if (
                        current_hash != stored_hash
                        or game.timeline.context != stored.timeline.context
                    ):
                        raise ReplayDivergenceError(
                            decision, "Checkpoint diverges from replayed actions"
                        )
        _require(
            game.timeline.context.decision_seq == decision_seq,
            "Replay did not reach requested decision",
        )
        return game

    def seek_event(self, event_seq):
        """Return an event and the prior/next executable decision boundaries."""
        initial = self._manifest["initial_context"]["event_seq"]
        final = self._manifest["final_context"]["event_seq"]
        _require(
            type(event_seq) is int and initial < event_seq <= final,
            "Event index is outside this replay",
        )
        self.last_seek_blocks = 0
        metadata = next(
            (
                block
                for block in self._manifest["blocks"]
                if block["event_start"] <= event_seq < block["event_stop"]
            ),
            None,
        )
        _require(metadata is not None, "Event index is not covered by a replay block")
        block = self._block(metadata)
        indexed = next(
            (item for item in block["event_index"] if item["event_seq"] == event_seq),
            None,
        )
        _require(indexed is not None, "Event index is missing")
        row = block["decisions"][indexed["decision_offset"]]
        event = row["events"][indexed["event_offset"]]
        game = self.seek_decision(indexed["previous_decision"])
        # Verify the causing decision on another private game. The returned
        # game remains at the explicitly advertised prior decision boundary.
        verification = clone_from_snapshot(
            capture_snapshot(game), adapters=self.adapters
        )
        control = ActionControl(verification, verification.timeline._entities)
        decision = indexed["next_decision"]
        try:
            if (
                snapshot_hash(capture_snapshot(verification), limits=self.limits)
                != row["pre_hash"]
            ):
                raise ReplayDivergenceError(decision, "Event pre-state diverged")
            result = verification.advance(
                control.decode(control.request(ActionV1.from_json(row["action"])))
            )
            if (
                len(result.decisions) != 1
                or result.decisions[0].to_json()
                != {
                    key: value
                    for key, value in row.items()
                    if key not in ("schema_version", "pre_hash", "post_hash")
                }
                or snapshot_hash(capture_snapshot(verification), limits=self.limits)
                != row["post_hash"]
            ):
                raise ReplayDivergenceError(decision, "Event consequences diverged")
        except ReplayDivergenceError:
            raise
        except Exception as error:
            raise ReplayDivergenceError(decision) from error
        self.last_seek_replayed += 1
        self.last_seek_blocks += 1
        return IndexedReplayEvent(
            deepcopy(event),
            deepcopy(event["context"]),
            indexed["previous_decision"],
            indexed["next_decision"],
            game,
        )

    def resume_at_decision(self, decision_seq, *, branch_id=None):
        """Create an independent fork without changing the factual replay."""
        game = self.seek_decision(decision_seq)
        old = game.timeline.context.to_json()
        branch_id = "resume-" + str(decision_seq) if branch_id is None else branch_id
        _id(branch_id)
        game.timeline.fork(branch_id)
        origin = {
            "replay_id": self._manifest["replay_id"],
            "decision_seq": decision_seq,
            "event_seq": old["event_seq"],
            "branch_id": old["branch_id"],
        }
        return ReplayContinuation(game, self._manifest["origin_family_id"], origin)


__all__ = [
    "IndexedReplayEvent",
    "LegacyReplayPermissionError",
    "LegacyReplayView",
    "ReplayContinuation",
    "ReplayDivergenceError",
    "ReplayError",
    "ReplayFormatError",
    "ReplayIncompatibleError",
    "ReplayReader",
    "ReplayRecorder",
    "open_legacy_replay",
]
