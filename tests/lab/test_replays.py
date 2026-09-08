"""Executable ReplayV1 fidelity, indexes, corruption, and resume boundaries."""

import hashlib
import json
import pickle
import random
import subprocess
import sys
from copy import deepcopy

import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.replays import (
    LegacyReplayPermissionError,
    ReplayContinuation,
    ReplayDivergenceError,
    ReplayFormatError,
    ReplayIncompatibleError,
    ReplayReader,
    ReplayRecorder,
    open_legacy_replay,
)
from botbowl.lab.snapshot_io import snapshot_hash
from botbowl.lab.snapshots import LogicalTime, capture_snapshot
from botbowl.lab.timeline import Timeline
from tests.baseline import SIZES, progress_action
from tests.lab.test_semantic_actions import semantic_game
from tests.lab.test_timeline import players, until


def record(tmp_path, *, size=1, decisions=7, interval=3, name="replay"):
    game = semantic_game(size)
    recorder = ReplayRecorder(
        game,
        tmp_path,
        name,
        replay_id=name,
        origin_family_id="fixture-family",
        checkpoint_interval=interval,
    )
    game.init()
    states = {0: deepcopy(game.state.to_json(ignore_clocks=True))}
    hashes = {0: snapshot_hash(capture_snapshot(game))}
    actors = []
    for _ in range(decisions):
        result = recorder.advance(progress_action(game))
        decision = result.decisions[0]
        actors.append(decision.actor_id)
        states[decision.after.decision_seq] = deepcopy(
            game.state.to_json(ignore_clocks=True)
        )
        hashes[decision.after.decision_seq] = snapshot_hash(capture_snapshot(game))
    manifest = recorder.close(truncation_reason="fixture_limit")
    return game, manifest, states, hashes, actors


@pytest.mark.parametrize("size", SIZES)
def test_five_sizes_both_teams_close_open_and_reproduce(tmp_path, size):
    game, manifest, states, hashes, actors = record(
        tmp_path, size=size, decisions=6, interval=2
    )
    assert set(actors) == {"home", "away"}
    before = pickle.dumps(game)
    reader = ReplayReader(tmp_path, "replay")
    fully_replayed = reader.replay_all()
    assert fully_replayed.timeline.to_json() == game.timeline.to_json()
    assert reader.last_seek_replayed == 6
    for decision in [0, 3, 6] + random.Random(42).sample(range(7), 4):
        restored = reader.seek_decision(decision)
        assert restored.state.to_json(ignore_clocks=True) == states[decision]
        assert snapshot_hash(capture_snapshot(restored)) == hashes[decision]
    assert pickle.dumps(game) == before
    assert manifest["descriptor"] == reader.manifest["descriptor"]


def test_fresh_process_replays_same_events_and_hash(tmp_path):
    source, manifest, _, hashes, _ = record(tmp_path, decisions=7, interval=3)
    output = tmp_path / "fresh-process.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.lab.replay_process",
            str(tmp_path),
            "replay",
            str(manifest["final_context"]["decision_seq"]),
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    evidence = json.loads(output.read_text())
    assert evidence["context"] == manifest["final_context"]
    assert evidence["hash"] == hashes[7]
    assert evidence["replayed"] == 7
    assert evidence["events"] == [event.to_json() for event in source.timeline.events]
    assert [event["context"]["event_seq"] for event in evidence["events"]] == list(
        range(1, manifest["final_context"]["event_seq"] + 1)
    )


def test_terminal_replay_closes_without_manufactured_truncation(tmp_path):
    game = semantic_game(1)
    recorder = ReplayRecorder(
        game,
        tmp_path,
        "terminal",
        replay_id="terminal",
        origin_family_id="terminal-family",
        checkpoint_interval=8,
    )
    game.init()
    for _ in range(180):
        if game.state.game_over:
            break
        recorder.advance(progress_action(game))
    assert game.state.game_over
    manifest = recorder.close()
    assert manifest["end"] == {"kind": "terminal", "reason": "game_over"}
    restored = ReplayReader(tmp_path, "terminal").seek_decision(
        manifest["final_context"]["decision_seq"]
    )
    assert restored.state.game_over


def test_seek_replays_pending_randomness_without_policy_calls(tmp_path):
    game = semantic_game(3)
    game.time_source = LogicalTime()
    Timeline(game, episode_id="random-source")
    game.init()
    until(game, lambda candidate: type(candidate.get_procedure()) is proc.Turn)
    player, _ = players(game, [(3, 3)], [(4, 3)])
    player.team.state.rerolls = 1
    player.extra_skills = []
    game.set_available_actions()
    game.dice.fix(bb.D6, 1, 6)
    start = game.timeline.context.decision_seq
    continuation = ReplayContinuation(
        game,
        "random-family",
        {
            "replay_id": "setup-prefix",
            "decision_seq": start,
            "event_seq": game.timeline.context.event_seq,
            "branch_id": game.timeline.context.branch_id,
        },
    )
    recorder = ReplayRecorder(
        continuation, tmp_path, "random", replay_id="random", checkpoint_interval=4
    )
    recorder.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    recorder.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
    assert type(game.get_procedure()) is proc.Reroll
    pending = deepcopy(game.state.to_json(ignore_clocks=True))
    recorder.advance(bb.Action(bb.ActionType.USE_REROLL))
    recorder.close(truncation_reason="random_probe")
    reader = ReplayReader(tmp_path, "random")
    restored = reader.seek_decision(start + 2)
    assert restored.state.to_json(ignore_clocks=True) == pending
    assert type(restored.get_procedure()) is proc.Reroll
    assert reader.last_seek_replayed == 2


def test_seek_uses_nearest_checkpoint_and_event_marks_automatic_interval(tmp_path):
    _, manifest, states, _, _ = record(tmp_path, decisions=8, interval=3)
    reader = ReplayReader(tmp_path, "replay")
    restored = reader.seek_decision(5)
    assert restored.state.to_json(ignore_clocks=True) == states[5]
    assert reader.last_seek_replayed == 2
    assert reader.last_seek_blocks == 1
    block = next(
        item
        for item in manifest["blocks"]
        if item["event_stop"] - item["event_start"] > 1
    )
    event_seq = block["event_start"] + 1
    indexed = reader.seek_event(event_seq)
    assert indexed.event["context"]["event_seq"] == event_seq
    assert indexed.context == indexed.event["context"]
    assert indexed.next_decision == indexed.previous_decision + 1
    assert indexed.game.timeline.context.decision_seq == indexed.previous_decision


def test_empty_and_truncated_replays_have_executable_initial_checkpoint(tmp_path):
    game = semantic_game(1)
    recorder = ReplayRecorder(
        game, tmp_path, "empty", replay_id="empty", origin_family_id="empty-family"
    )
    game.init()
    manifest = recorder.close(truncation_reason="empty_fixture")
    assert manifest["blocks"] == []
    assert manifest["indexes"]["decisions"] == []
    assert manifest["end"] == {"kind": "truncated", "reason": "empty_fixture"}
    restored = ReplayReader(tmp_path, "empty").seek_decision(0)
    assert restored.timeline.context.decision_seq == 0


def test_resume_is_independent_preserves_family_and_records_origin(tmp_path):
    source, _, states, _, _ = record(tmp_path, decisions=6, interval=2)
    before = pickle.dumps(source)
    reader = ReplayReader(tmp_path, "replay")
    continuation = reader.resume_at_decision(3, branch_id="alternative")
    assert continuation.origin_family_id == "fixture-family"
    assert continuation.origin == {
        "replay_id": "replay",
        "decision_seq": 3,
        "event_seq": continuation.game.timeline.context.event_seq,
        "branch_id": "root",
    }
    assert continuation.game.timeline.context.branch_id == "alternative"
    derived = ReplayRecorder(
        continuation, tmp_path, "derived", replay_id="derived", checkpoint_interval=2
    )
    derived.advance(progress_action(continuation.game))
    derived_manifest = derived.close(truncation_reason="branch_probe")
    assert derived_manifest["origin_family_id"] == "fixture-family"
    assert derived_manifest["origin"]["replay_id"] == "replay"
    assert pickle.dumps(source) == before
    assert reader.seek_decision(3).state.to_json(ignore_clocks=True) == states[3]


def _rewrite_json(path, document):
    raw = (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    path.write_bytes(raw)
    return len(raw), hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("damage", ("repeated", "lost", "event-index"))
def test_repeated_lost_and_misindexed_records_are_rejected(tmp_path, damage):
    _, manifest, _, _, _ = record(tmp_path, decisions=4, interval=4)
    block_meta = manifest["blocks"][0]
    block_path = tmp_path / "replay" / block_meta["path"]
    block = json.loads(block_path.read_text())
    if damage == "repeated":
        block["decisions"][1]["after"]["decision_seq"] = 1
    elif damage == "lost":
        block["decisions"].pop(1)
    else:
        block["event_index"][0]["event_seq"] += 1
    size, digest = _rewrite_json(block_path, block)
    manifest_path = tmp_path / "replay" / "manifest.json"
    current = json.loads(manifest_path.read_text())
    current["blocks"][0]["bytes"] = size
    current["blocks"][0]["sha256"] = digest
    _rewrite_json(manifest_path, current)
    with pytest.raises(ReplayFormatError):
        ReplayReader(tmp_path, "replay").seek_decision(1)


@pytest.mark.parametrize("damage", ("cut-block", "cut-checkpoint", "version", "index"))
def test_cut_files_incompatible_version_and_invalid_index_are_rejected(
    tmp_path, damage
):
    _, manifest, _, _, _ = record(tmp_path, decisions=4, interval=4)
    manifest_path = tmp_path / "replay" / "manifest.json"
    if damage == "cut-block":
        path = tmp_path / "replay" / manifest["blocks"][0]["path"]
        path.write_bytes(path.read_bytes()[:-7])
        with pytest.raises(ReplayFormatError):
            ReplayReader(tmp_path, "replay").seek_decision(1)
    elif damage == "cut-checkpoint":
        path = tmp_path / "replay" / manifest["checkpoints"][0]["path"]
        path.write_bytes(path.read_bytes()[:-7])
        with pytest.raises(ReplayFormatError):
            ReplayReader(tmp_path, "replay").seek_decision(0)
    else:
        document = json.loads(manifest_path.read_text())
        if damage == "version":
            document["version"] = 2
        else:
            document["indexes"]["decisions"][0]["start"] += 1
        _rewrite_json(manifest_path, document)
        error = ReplayIncompatibleError if damage == "version" else ReplayFormatError
        with pytest.raises(error):
            ReplayReader(tmp_path, "replay")


@pytest.mark.parametrize("checkpoint,requested", ((0, 1), (3, 5)))
def test_corrupt_checkpoint_reports_its_boundary_not_seek_target(
    tmp_path, checkpoint, requested
):
    _, manifest, _, _, _ = record(tmp_path, decisions=6, interval=3)
    metadata = next(
        item for item in manifest["checkpoints"] if item["decision_seq"] == checkpoint
    )
    path = tmp_path / "replay" / metadata["path"]
    path.write_bytes(path.read_bytes()[:-7])
    with pytest.raises(ReplayFormatError) as caught:
        ReplayReader(tmp_path, "replay").seek_decision(requested)
    assert caught.value.decision_seq == checkpoint


def test_first_hash_divergence_reports_exact_decision(tmp_path):
    _, manifest, _, _, _ = record(tmp_path, decisions=4, interval=4)
    block_meta = manifest["blocks"][0]
    block_path = tmp_path / "replay" / block_meta["path"]
    block = json.loads(block_path.read_text())
    block["decisions"][1]["post_hash"] = "sha256:" + "0" * 64
    size, digest = _rewrite_json(block_path, block)
    manifest_path = tmp_path / "replay" / "manifest.json"
    current = json.loads(manifest_path.read_text())
    current["blocks"][0].update(bytes=size, sha256=digest)
    _rewrite_json(manifest_path, current)
    with pytest.raises(ReplayDivergenceError) as caught:
        ReplayReader(tmp_path, "replay").seek_decision(3)
    assert caught.value.decision_seq == 2


def test_unconfirmed_partial_and_direct_game_advance_are_not_executable(tmp_path):
    game = semantic_game(1)
    recorder = ReplayRecorder(
        game, tmp_path, "partial", replay_id="partial", origin_family_id="family"
    )
    game.init()
    recorder._ensure_started()
    with pytest.raises(ReplayFormatError):
        ReplayReader(tmp_path, "partial.partial")
    game.advance(progress_action(game))
    with pytest.raises(ReplayFormatError, match="outside ReplayRecorder"):
        recorder.close(truncation_reason="fixture")


def test_legacy_replay_requires_explicit_trust_and_stays_visual_only():
    legacy = bb.Replay("legacy")
    legacy.steps[0] = bb.ReplayStep({"frame": 0}, 0)
    with pytest.raises(LegacyReplayPermissionError):
        open_legacy_replay(legacy)
    view = open_legacy_replay(legacy, trusted=True)
    assert view.executable is False
    assert view.first().game == {"frame": 0}
    assert not hasattr(view, "resume_at_decision")


def test_append_flush_and_seek_only_read_selected_block(tmp_path, monkeypatch):
    import botbowl.lab.replays as replay_module

    game = semantic_game(1)
    recorder = ReplayRecorder(
        game,
        tmp_path,
        "flush",
        replay_id="flush",
        origin_family_id="family",
        checkpoint_interval=4,
    )
    game.init()
    recorder.append(progress_action(game))
    recorder.flush()
    assert len(list((tmp_path / "flush.partial" / "blocks").iterdir())) == 1
    for _ in range(6):
        recorder.append(progress_action(game))
    recorder.close(truncation_reason="probe")
    reader = ReplayReader(tmp_path, "flush")
    opened, executed = [], []
    original_read, original_advance = replay_module._read_file, bb.Game.advance

    def read(path, limit):
        opened.append(str(path))
        return original_read(path, limit)

    def advance(game, *args, **kwargs):
        executed.append(game.timeline.context.decision_seq)
        return original_advance(game, *args, **kwargs)

    monkeypatch.setattr(replay_module, "_read_file", read)
    monkeypatch.setattr(bb.Game, "advance", advance)
    reader.seek_decision(6)
    assert executed == [4, 5]
    assert reader.last_seek_replayed == len(executed)
    assert not any(
        "block-00000000" in path or "block-00000001" in path for path in opened
    )
    assert sum("blocks/" in path for path in opened) == 1


@pytest.mark.parametrize(
    "damage", ["family", "descriptor", "end", "missing-checkpoint"]
)
def test_manifest_cannot_relabel_snapshot_provenance_or_end(tmp_path, damage):
    _, manifest, _, _, _ = record(tmp_path, decisions=3, interval=3)
    if damage == "family":
        manifest["origin_family_id"] = "forged-family"
    elif damage == "descriptor":
        manifest["descriptor"]["engine_version"] = "999"
    elif damage == "end":
        manifest["end"] = {"kind": "terminal", "reason": "game_over"}
    else:
        manifest["checkpoints"].pop()
    _rewrite_json(tmp_path / "replay" / "manifest.json", manifest)
    with pytest.raises(ReplayFormatError):
        ReplayReader(tmp_path, "replay").seek_decision(3)


def test_close_rejects_unrecorded_state_change_at_checkpoint(tmp_path):
    game = semantic_game(1)
    recorder = ReplayRecorder(
        game,
        tmp_path,
        "changed",
        replay_id="changed",
        origin_family_id="family",
        checkpoint_interval=1,
    )
    game.init()
    recorder.append(progress_action(game))
    game.rng.rand()
    with pytest.raises(ReplayFormatError, match="final state changed"):
        recorder.close(truncation_reason="probe")


def test_snapshot_version_rejected_with_checkpoint_index(tmp_path):
    from botbowl.lab.snapshot_io import _digest

    _, manifest, _, _, _ = record(tmp_path, decisions=2, interval=2)
    checkpoint = manifest["checkpoints"][0]
    path = tmp_path / "replay" / checkpoint["path"]
    document = json.loads(path.read_text())
    document["component_versions"]["graph"] = 999
    document["payload_digest"] = _digest(
        {k: v for k, v in document.items() if k != "payload_digest"}
    )
    checkpoint["bytes"], checkpoint["sha256"] = _rewrite_json(path, document)
    checkpoint["payload_digest"] = document["payload_digest"]
    _rewrite_json(tmp_path / "replay" / "manifest.json", manifest)
    with pytest.raises(ReplayIncompatibleError) as caught:
        ReplayReader(tmp_path, "replay").seek_decision(0)
    assert caught.value.decision_seq == 0


def test_invalid_decision_and_event_indices(tmp_path):
    record(tmp_path, decisions=2, interval=2)
    reader = ReplayReader(tmp_path, "replay")
    for value in (-1, 3, True, 1.0, "1"):
        with pytest.raises(ReplayFormatError):
            reader.seek_decision(value)
    for value in (0, -1, True, 1.0, "1", 10**10):
        with pytest.raises(ReplayFormatError):
            reader.seek_event(value)


def test_atomic_block_failure_never_confirms_or_retries_action(tmp_path, monkeypatch):
    import botbowl.lab.replays as replay_module

    game = semantic_game(1)
    recorder = ReplayRecorder(
        game,
        tmp_path,
        "failure",
        replay_id="failure",
        origin_family_id="family",
        checkpoint_interval=1,
    )
    game.init()

    def fail(*args, **kwargs):
        raise OSError("simulated block write failure")

    monkeypatch.setattr(replay_module, "_atomic_json", fail)
    with pytest.raises(OSError):
        recorder.append(progress_action(game))
    assert game.timeline.context.decision_seq == 1
    assert recorder.failed
    assert not (tmp_path / "failure").exists()
    with pytest.raises(ReplayFormatError):
        ReplayReader(tmp_path, "failure.partial")
