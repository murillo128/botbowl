"""Isolated representation and replay experiments; never imported by the engine."""
import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass
import json
import gc
from pathlib import Path
import pickle
import sys
import tempfile
import time
import tracemalloc
from unittest.mock import patch

from botbowl.core.model import Square
from botbowl.core.forward_model import immutable_types, immutable_after_init
from .headless import summary, write_bytes, digest


class SquareMethods:
    __slots__ = ()
    out_of_bounds = Square.out_of_bounds
    to_json = Square.to_json
    __eq__ = Square.__eq__
    __hash__ = Square.__hash__
    distance = Square.distance
    is_adjacent = Square.is_adjacent
    __repr__ = Square.__repr__


class SlotSquare(SquareMethods):
    __slots__ = ('x', 'y', '_out_of_bounds')

    def __init__(self, x, y, _out_of_bounds=None):
        object.__setattr__(self, 'x', x)
        object.__setattr__(self, 'y', y)
        object.__setattr__(self, '_out_of_bounds', _out_of_bounds)

    def __setattr__(self, key, value):
        raise AttributeError('Immutable experiment')

    def __reduce__(self):
        # Required: default slot restoration would hit our immutable setter.
        return type(self), (self.x, self.y, self._out_of_bounds)


@dataclass(frozen=True, eq=False, repr=False)
class DataSquare(SquareMethods):
    x: int
    y: int
    _out_of_bounds: object = None


@dataclass(frozen=True)
class NaiveDataSquare:
    x: int
    y: int
    _out_of_bounds: object = None


@contextmanager
def square_variant(cls):
    """Process-local experiment replaces imported aliases and restores all of them.

    Not thread-safe; exact-type checks make replacing only model.Square invalid.
    Existing objects must not cross this boundary. Use a fresh game inside it.
    """
    registered = cls in immutable_types
    immutable_types.add(cls)
    try:
        with ExitStack() as stack:
            for name, module in list(sys.modules.items()):
                if module is None or not (name == 'botbowl' or name.startswith('botbowl.')
                                          or name.startswith('tests.')):
                    continue
                if name.startswith('botbowl.benchmarks'):
                    continue
                for key, value in list(vars(module).items()):
                    if value is Square:
                        stack.enter_context(patch.object(module, key, cls))
            yield
    finally:
        if not registered:
            immutable_types.discard(cls)


def delta(previous, current):
    """Dictionary field replacements/removals; lists replaced atomically.

    This intentionally small JSON experiment is not a new persistent API.
    """
    if isinstance(previous, dict) and isinstance(current, dict):
        changes = {key: delta(previous[key], value) if key in previous else {'set': value}
                   for key, value in current.items()
                   if key not in previous or previous[key] != value}
        return {'changes': changes, 'remove': [key for key in previous if key not in current]}
    return {'set': current}


def apply_delta(previous, change):
    if 'set' in change:
        return deepcopy(change['set'])
    result = deepcopy(previous)
    for key in change['remove']:
        del result[key]
    for key, value in change['changes'].items():
        result[key] = apply_delta(result.get(key), value)
    return result


def encode_frames(frames, interval):
    if interval < 1:
        raise ValueError('Checkpoint interval must be positive')
    return [{'checkpoint': frame} if i % interval == 0 else {'delta': delta(frames[i-1], frame)}
            for i, frame in enumerate(frames)]


def decode_frames(records):
    frames = []
    for record in records:
        frames.append(deepcopy(record['checkpoint']) if 'checkpoint' in record
                      else apply_delta(frames[-1], record['delta']))
    return frames


def seek_frame(records, index):
    start = index
    while 'checkpoint' not in records[start]:
        start -= 1
    return decode_frames(records[start:index+1])[-1]


def measure(function, repetitions, warmup=1):
    for _ in range(warmup):
        function()
    values = []
    for _ in range(repetitions):
        gc.collect()
        start = time.perf_counter_ns()
        value = function()
        values.append(time.perf_counter_ns() - start)
    gc.collect()
    tracemalloc.start()
    try:
        memory_value = function()  # Retain the result through peak measurement.
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del memory_value
    return value, dict(time_ns=summary(values), peak_python_bytes=peak)


def representation_report(repetitions, count=10000):
    report = {}
    for cls in [Square, SlotSquare, DataSquare]:
        objects, construction = measure(lambda: [cls(i % 28, (i // 28) % 17, False)
                                                 for i in range(count)], repetitions)
        copied, copying = measure(lambda: deepcopy(objects), repetitions)
        payload, encoding = measure(lambda: pickle.dumps(objects), repetitions)
        value = objects[0]
        assert copied == objects and copied[0] is not value
        assert pickle.loads(payload) == objects
        assert value == Square(0, 0, True) and hash(value) == hash(Square(0, 0, True))
        report[cls.__name__] = dict(count=count, construction=construction, deepcopy=copying,
                                   pickle=encoding, pickle_bytes=len(payload),
                                   instance_bytes=sys.getsizeof(value),
                                   dictionary_bytes=sys.getsizeof(value.__dict__) if hasattr(value, '__dict__') else 0,
                                   json=digest([v.to_json() for v in objects]),
                                   pickle_class=cls.__module__ + '.' + cls.__name__)
    # Record why mechanical substitutions are invalid, rather than timing them as wins.
    class NaiveSlots:
        __slots__ = ('x', 'y', '_out_of_bounds')

        def __init__(self, x, y, _out_of_bounds=None):
            self.x, self.y, self._out_of_bounds = x, y, _out_of_bounds

    decorated = immutable_after_init(NaiveSlots)
    try:
        decorated(1, 2)
    except AttributeError as exc:
        report['naive_slots_error'] = str(exc)
    finally:
        immutable_types.discard(decorated)
    report['naive_dataclass'] = dict(
        cross_type_equal=NaiveDataSquare(1, 2) == Square(1, 2),
        bounds_ignored=NaiveDataSquare(1, 2, False) == NaiveDataSquare(1, 2, True),
        hash_compatible=hash(NaiveDataSquare(1, 2)) == hash(Square(1, 2)))
    return report


def replay_report(frames, repetitions, temp_root=None):
    report = dict(frame_count=len(frames), corpus_sha256=digest(frames), formats={})
    with tempfile.TemporaryDirectory(prefix='botbowl-deltas-', dir=temp_root) as directory:
        for interval in [1, 4, 16, 64]:
            records, encoding = measure(lambda: encode_frames(frames, interval), repetitions)
            restored, decoding = measure(lambda: decode_frames(records), repetitions)
            assert restored == frames
            assert all(seek_frame(records, i) == frame for i, frame in enumerate(frames))
            seek_indices = sorted(set([0, len(frames) - 1] +
                                      [i * len(frames) // 8 for i in range(1, 8)]))
            seeks, seeking = measure(lambda: [seek_frame(records, i) for i in seek_indices], repetitions)
            assert seeks == [frames[i] for i in seek_indices]
            payload, serialization = measure(lambda: pickle.dumps(records), repetitions)
            json_payload = json.dumps(records, separators=(',', ':')).encode()
            assert decode_frames(pickle.loads(payload)) == frames
            assert decode_frames(json.loads(json_payload)) == frames
            size, writing = measure(lambda: write_bytes(Path(directory) / 'prototype.rep', payload), repetitions)
            report['formats'][str(interval)] = dict(encode=encoding, decode=decoding, seek_sample=seeking,
                                                   seek_indices=seek_indices,
                                                   pickle=serialization, write=writing,
                                                   pickle_bytes=size, json_bytes=len(json_payload))
        # Same frame corpus, using the actual existing Replay/ReplayStep container.
        from botbowl.core.model import Replay, ReplayStep
        replay = Replay('prototype-baseline')
        replay.steps = {i: ReplayStep(frame, len(frame['state']['reports']))
                        for i, frame in enumerate(frames)}
        payload, baseline = measure(lambda: pickle.dumps(replay), repetitions)
        report['legacy_frame_container'] = dict(pickle=baseline, pickle_bytes=len(payload))
    report['semantic_identity'] = True
    return report


def compact_replay(replay, interval):
    keys = list(replay.steps)
    return dict(replay_id=replay.replay_id, ids=keys,
                records=encode_frames([replay.steps[k].game for k in keys], interval),
                num_reports=[replay.steps[k].num_reports for k in keys],
                actions=replay.actions, reports=replay.reports, idx=replay.idx)


def expand_replay(compact):
    from botbowl.core.model import Replay, ReplayStep
    replay = Replay(compact['replay_id'])
    replay.steps = {key: ReplayStep(frame, reports) for key, frame, reports in zip(
        compact['ids'], decode_frames(compact['records']), compact['num_reports'])}
    replay.actions = deepcopy(compact['actions'])
    replay.reports = deepcopy(compact['reports'])
    replay.idx = compact['idx']
    return replay


def legacy_replay_report(replays, repetitions, temp_root=None):
    """Compare the real out-of-line reports/actions format to an isolated envelope."""
    report = {}
    with tempfile.TemporaryDirectory(prefix='botbowl-legacy-delta-', dir=temp_root) as directory:
        payloads, serializing = measure(lambda: [pickle.dumps(r) for r in replays], repetitions)
        _, writing = measure(lambda: [write_bytes(Path(directory) / (str(i) + '.rep'), payload)
                                      for i, payload in enumerate(payloads)], repetitions)
        _, loading = measure(lambda: [pickle.loads(payload) for payload in payloads], repetitions)
        report['legacy'] = dict(bytes=sum(map(len, payloads)), pickle=serializing,
                                write=writing, load=loading)
        for interval in [4, 16, 64]:
            records, encoding = measure(lambda: [compact_replay(r, interval) for r in replays], repetitions)
            payloads, serializing = measure(lambda: [pickle.dumps(r) for r in records], repetitions)
            restored, decoding = measure(lambda: [expand_replay(pickle.loads(p)) for p in payloads], repetitions)
            for original, restored_replay in zip(replays, restored):
                assert original.to_json() == restored_replay.to_json()
                assert list(original.steps) == list(restored_replay.steps)
                assert [s.num_reports for s in original.steps.values()] == [s.num_reports for s in restored_replay.steps.values()]
                assert [r.to_json() for r in original.reports] == [r.to_json() for r in restored_replay.reports]
                assert original.idx == restored_replay.idx
            _, writing = measure(lambda: [write_bytes(Path(directory) / (str(i) + '.rep'), payload)
                                          for i, payload in enumerate(payloads)], repetitions)
            report[str(interval)] = dict(bytes=sum(map(len, payloads)), encode=encoding,
                                         pickle=serializing, write=writing, load_expand=decoding)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=Path)
    parser.add_argument('--legacy-replays', type=Path,
                        help='Trusted local pickle produced by the headless harness only')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--regressions', choices=['slots', 'dataclass'])
    parser.add_argument('--engine', choices=['slots', 'dataclass'])
    args, pytest_args = parser.parse_known_args()
    if args.regressions:
        import pytest
        with square_variant(SlotSquare if args.regressions == 'slots' else DataSquare):
            raise SystemExit(pytest.main(pytest_args))
    if args.engine:
        from .headless import benchmark
        if args.output is None:
            parser.error('--output required')
        with square_variant(SlotSquare if args.engine == 'slots' else DataSquare):
            report, _ = benchmark(repetitions=args.repetitions,
                                  progress=lambda message: print(message, flush=True))
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        return
    if args.frames is None or args.output is None or args.repetitions < 2:
        parser.error('--frames, --output and >=2 repetitions required')
    report = dict(schema_version=1, repetitions=args.repetitions, warmup=1,
                  representation=representation_report(args.repetitions),
                  replay=replay_report(json.loads(args.frames.read_text()), args.repetitions))
    if args.legacy_replays:
        report['legacy_replays'] = legacy_replay_report(pickle.loads(args.legacy_replays.read_bytes()), args.repetitions)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
