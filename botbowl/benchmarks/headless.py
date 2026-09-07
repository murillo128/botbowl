"""Reproducible decision-boundary benchmark: python -m botbowl.benchmarks.headless."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import gc
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import pickle
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from types import FunctionType
from unittest.mock import patch

import botbowl
from botbowl.core import procedure
from botbowl.core.game import Game
from botbowl.core.model import Action, ActionType, Agent, Replay
from botbowl.lab.observations import ObservationControl, observe


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def summary(values):
    return dict(samples=values, median=statistics.median(values),
                min=min(values), max=max(values),
                stdev=statistics.stdev(values) if len(values) > 1 else 0)


class Meter:
    def __init__(self):
        self.ns = defaultdict(int)
        self.counts = defaultdict(int)

    def call(self, name, function, *args, **kwargs):
        start = time.perf_counter_ns()
        result = function(*args, **kwargs)
        self.ns[name] += time.perf_counter_ns() - start
        self.counts[name] += 1
        return result


@contextmanager
def backend(name):
    """Select engine procedure pathfinding locally; never silently fall back."""
    module = importlib.import_module('botbowl.core.pathfinding.' +
                                    ('python_pathfinding' if name == 'python' else 'cython_pathfinding'))
    with patch.object(procedure, 'Pathfinder', module.Pathfinder):
        yield module


def fixture(seed):
    config = botbowl.load_config('gym-11')
    config.kick_off_table = False
    config.pathfinding_enabled = True
    config.fast_mode = True
    # RuleSet's inherited mutable defaults accumulate definitions across stock
    # loader calls. Use the stock parser with private constructor inputs (the
    # same isolation pattern as the issue-20 investigation fixture). Neither
    # patch the loader nor change the engine's defaults during this experiment.
    def empty_ruleset(name):
        return botbowl.RuleSet(name, races=[], star_players=[], inducements=[],
                               spp_actions={}, spp_levels={}, improvements={})

    loader = botbowl.load_rule_set
    isolated_loader = FunctionType(loader.__code__, dict(loader.__globals__, RuleSet=empty_ruleset),
                                   argdefs=loader.__defaults__, closure=loader.__closure__)
    rules = deepcopy(isolated_loader(config.ruleset))
    teams = []
    for side, race in [('home', 'human'), ('away', 'orc')]:
        team = botbowl.load_team_by_filename(race, rules)
        team.team_id = side
        for index, player in enumerate(team.players):
            player.player_id = '{}-{}'.format(side, index)
        teams.append(team)
    game = Game('benchmark-{}'.format(seed), *teams,
                Agent('home', human=True, agent_id='home-agent'),
                Agent('away', human=True, agent_id='away-agent'), config,
                ruleset=rules, seed=seed)
    game.init()
    for action_type in [ActionType.START_GAME, ActionType.HEADS, ActionType.KICK,
                        ActionType.SETUP_FORMATION_ZONE, ActionType.END_SETUP,
                        ActionType.SETUP_FORMATION_WEDGE, ActionType.END_SETUP]:
        game.step(Action(action_type))
    # Clocks are disabled; normalize only the wall-clock game start field.
    game.start_time = None
    game.arena.to_json()  # Exclude the arena's one-time serialization cache.
    return game


def choose(game, rng):
    """Uniform choices, then positions/players; ordered, legal, separate RNG."""
    choices = sorted((c for c in game.get_available_actions()
                      if not c.disabled and c.action_type != ActionType.PLACE_PLAYER),
                     key=lambda c: c.action_type.name)
    for _ in range(1000):
        choice = rng.choice(choices)
        positions = sorted(choice.positions, key=lambda p: (p.y, p.x))
        players = sorted(choice.players, key=lambda p: p.player_id)
        action = Action(choice.action_type,
                        position=rng.choice(positions) if positions else None,
                        player=rng.choice(players) if players else None)
        if game.is_action_allowed(action):
            return action
    raise RuntimeError('Policy could not select a legal action')


def state_json(game):
    value = game.to_json()
    value['start_time'] = value['end_time'] = None
    # Clocks are external to trajectory checkpoints, even in noncompetition games.
    value['state']['clocks'] = []
    return value


@contextmanager
def instrumentation(meter):
    # _one_step includes procedure lifecycle and legal-action generation.
    # These inclusive timers overlap: do not add them to decision_step.
    from contextlib import ExitStack
    targets = [(Game, '_one_step', 'internal_one_step'),
               (Game, 'set_available_actions', 'legal_actions')]
    targets += [(cls, 'step', 'procedure_step') for cls in vars(procedure).values()
                if isinstance(cls, type) and cls.__module__ == procedure.__name__
                and 'step' in cls.__dict__]
    with ExitStack() as stack:
        for cls, method, label in targets:
            original = getattr(cls, method)

            def wrapped(self, *args, _original=original, _label=label, **kwargs):
                return meter.call(_label, _original, self, *args, **kwargs)

            stack.enter_context(patch.object(cls, method, wrapped))
        yield


def write_bytes(path, payload):
    # Buffered local write+close, explicitly no fsync/durability claim.
    with open(path, 'wb') as stream:
        stream.write(payload)
    return Path(path).stat().st_size


def run_sample(name, seeds, decisions, measured=True, temp_root=None):
    gc.collect()  # Previous samples may contain cyclic game/procedure graphs.
    meter = Meter()
    traces, frames, replay_sizes, outputs = [], [], [], []
    with backend(name) as paths, patch('time.time', return_value=0), tempfile.TemporaryDirectory(
            prefix='botbowl-benchmark-', dir=temp_root) as directory:
        for seed in seeds:
            # Python supplies a fixed input corpus to BOTH backends. Each timed
            # decision uses a fresh clone; a backend's output never becomes the
            # next input. This also exposes differing equal-cost path choices.
            with backend('python'):
                game = fixture(seed)
            control = ObservationControl(game)
            rng = random.Random(seed + 10000)
            replay = Replay(game.game_id)
            trace = []
            for _ in range(decisions):
                if game.state.game_over:
                    raise RuntimeError('Requested corpus extends beyond game end')
                action = meter.call('policy', choose, game, rng)
                before = state_json(game)
                decision_game = deepcopy(game)  # Input preparation, outside timers.
                decision_action = Action(action.action_type, position=action.position,
                                         player=decision_game.get_player(action.player.player_id)
                                         if action.player else None)
                if measured:
                    unprofiled_game = deepcopy(game)
                    meter.call('decision_step_uninstrumented', unprofiled_game.step, decision_action)
                    meter.call('legal_actions_read', game.get_available_actions)
                    players = sorted(game.get_players_on_pitch(), key=lambda p: p.player_id)
                    if players:
                        identities = [(id(p), id(p.state), id(p.position)) for p in players]
                        rng_before = pickle.dumps(game.capture_rng_state())
                        result = meter.call('pathfinding_query',
                                            paths.get_all_paths, game, players[0])
                        # Queries must not change even RNG or live object identity.
                        assert all(0 <= path.prob <= 1 for path in result)
                        assert identities == [(id(p), id(p.state), id(p.position)) for p in players]
                        assert pickle.dumps(game.capture_rng_state()) == rng_before
                    observation = meter.call('observation', observe, game, control, 'home')
                    meter.call('observation_to_json', observation.to_json)
                    clone = meter.call('deepcopy', deepcopy, game)
                    assert state_json(clone) == before
                    clone.enable_forward_model()
                    checkpoint = meter.call('checkpoint_capture', clone.capture_checkpoint)
                    clone_action = Action(action.action_type, position=action.position,
                                          player=clone.get_player(action.player.player_id)
                                          if action.player else None)
                    clone.step(clone_action)
                    clone_after = state_json(clone)
                    meter.call('checkpoint_restore', clone.restore_checkpoint, checkpoint)
                    assert state_json(clone) == before
                    clone.step(clone_action)
                    assert state_json(clone) == clone_after
                    value = meter.call('state_to_json', game.to_json)
                    meter.call('json_encode', json.dumps, value, sort_keys=True)
                    meter.call('replay_record_step', replay.record_step, game)
                    replay.record_action(action)
                    assert state_json(game) == before
                    with instrumentation(meter):
                        meter.call('decision_step', decision_game.step, decision_action)
                    assert state_json(decision_game) == clone_after
                    assert state_json(decision_game) == state_json(unprofiled_game)
                else:
                    decision_game.step(decision_action)
                after = state_json(decision_game)
                trace.append(dict(action=action.to_json(), state=after,
                                  rng=hashlib.sha256(pickle.dumps(decision_game.capture_rng_state())).hexdigest()))
                frames.append(before)
                with backend('python'):
                    game.step(action)
                # Do not charge an earlier decision's unreachable clone graphs
                # to a later timer/backend. Keep ordinary GC enabled in operations.
                del decision_game, decision_action
                if measured:
                    del clone, clone_action, unprofiled_game
                gc.collect()
            traces.append(digest(trace))
            outputs.extend(trace)
            if measured:
                replay.reports = game.state.reports
                payload = meter.call('replay_pickle', pickle.dumps, replay)
                replay_sizes.append(meter.call('replay_write', write_bytes,
                                              Path(directory) / (game.game_id + '.rep'), payload))
                restored = pickle.loads(payload)
                assert restored.to_json() == replay.to_json()
    return dict(ns=dict(meter.ns), counts=dict(meter.counts), traces=traces,
                replay_bytes=replay_sizes, outputs=outputs), frames


def environment():
    def command(args):
        try:
            return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    cpu = platform.processor()
    if Path('/proc/cpuinfo').exists():
        cpu = next((line.split(':', 1)[1].strip() for line in
                    Path('/proc/cpuinfo').read_text().splitlines()
                    if line.startswith('model name')), cpu)
    versions = {}
    for package in ['botbowl', 'numpy', 'Cython', 'pytest', 'Flask', 'playwright']:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    root = Path(__file__).resolve().parents[1]
    inputs = list((root / 'benchmarks').glob('*.py'))
    inputs += [root / 'data/config/gym-11.json', root / 'data/arenas/ff-pitch-11.txt']
    inputs += list((root / 'data/teams/11').glob('*.json'))
    inputs += list((root / 'data/rules').glob('*.xml'))
    return dict(python=sys.version, platform=platform.platform(), cpu=cpu,
                logical_cpus=os.cpu_count(), affinity=sorted(os.sched_getaffinity(0))
                if hasattr(os, 'sched_getaffinity') else None,
                versions=versions, compiler=command(['c++', '--version']),
                revision=command(['git', 'rev-parse', 'HEAD']),
                tracked_diff_sha256=hashlib.sha256((command(['git', 'diff', 'HEAD']) or '').encode()).hexdigest(),
                input_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in inputs},
                load_average=list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
                meminfo=Path('/proc/meminfo').read_text() if Path('/proc/meminfo').exists() else None,
                clock='perf_counter_ns', gc='enabled; explicit collection between decisions outside timers',
                memory='separate tracemalloc pass; Python allocations only',
                write='buffered write+close; no fsync; temporary directory on local filesystem')


def legacy_replays(seeds, decisions):
    """Actual Replay objects over the same fixed decision boundaries."""
    replays = []
    with backend('python'), patch('time.time', return_value=0):
        for seed in seeds:
            game = fixture(seed)
            replay = Replay(game.game_id)
            rng = random.Random(seed + 10000)
            for _ in range(decisions):
                action = choose(game, rng)
                replay.record_step(game)
                replay.record_action(action)
                game.step(action)
            replay.reports = game.state.reports
            replays.append(replay)
    return replays


def benchmark(seeds=(7, 17, 27), decisions=48, repetitions=5, warmup=1,
              backends=('python', 'native'), temp_root=None, progress=None):
    if decisions < 1 or repetitions < 2 or warmup < 0 or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError('Positive decisions, >=2 repetitions, nonnegative warmup and unique seeds required')
    report = dict(schema_version=1, environment=environment(),
                  corpus=dict(fixture='gym-11-human-orc-zone-wedge-v1', seeds=list(seeds),
                              policy='ordered-legal-uniform-v1', policy_seeds=[s + 10000 for s in seeds],
                              decisions_per_seed=decisions, config='gym-11',
                              ruleset_counts=dict(races=24, star_players=71, inducements=8,
                                                  spp_actions=5, spp_levels=7, improvements=6),
                              overrides=dict(kick_off_table=False, pathfinding_enabled=True, fast_mode=True)),
                  repetitions=repetitions, warmup=warmup, backends={})
    corpus_hash = None
    backend_traces = []
    outputs = {}
    for name in backends:
        if progress:
            progress(name + ': reference and warmup')
        plain, frames = run_sample(name, seeds, decisions, False, temp_root)
        reference = plain['traces']
        if corpus_hash is None:
            corpus_hash = digest(frames)
        assert digest(frames) == corpus_hash, 'Backend input corpus mismatch'
        backend_traces.append(reference)
        outputs[name] = plain['outputs']
        for _ in range(warmup):
            run_sample(name, seeds, decisions, temp_root=temp_root)
        samples = []
        for index in range(repetitions):
            if progress:
                progress('{}: repetition {}/{}'.format(name, index + 1, repetitions))
            result, _ = run_sample(name, seeds, decisions, temp_root=temp_root)
            assert result['traces'] == reference, 'Instrumentation changed semantics'
            samples.append(result)
        assert all(s['counts'] == samples[0]['counts'] for s in samples)
        memory_decisions = min(decisions, 8)
        memory_reference, _ = run_sample(name, seeds[:1], memory_decisions, False, temp_root)
        if progress:
            progress(name + ': separate memory sample (first seed, up to 8 decisions)')
        tracemalloc.start()
        try:
            memory_sample, _ = run_sample(name, seeds[:1], memory_decisions, temp_root=temp_root)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert memory_sample['traces'] == memory_reference['traces']
        with backend(name) as module:
            module_file = module.__file__
        report['backends'][name] = dict(module=module_file, counts=samples[0]['counts'],
                                      times_ns={key: summary([s['ns'][key] for s in samples])
                                                for key in samples[0]['ns']},
                                      peak_python_bytes=peak, traces=reference,
                                      memory_sample=dict(seed=seeds[0], decisions=memory_decisions),
                                      replay_bytes=samples[0]['replay_bytes'])
    report['corpus']['sha256'] = corpus_hash
    report['corpus']['driver_backend'] = 'python'
    report['semantic_identity'] = True  # Instrumented vs plain, within each backend.
    report['backend_outputs_identical'] = all(t == backend_traces[0] for t in backend_traces)
    if 'python' in outputs and 'native' in outputs:
        differing = [i for i, pair in enumerate(zip(outputs['python'], outputs['native']))
                     if pair[0] != pair[1]]
        report['backend_differences'] = dict(decision_indices=differing)
        if differing:
            index = differing[0]
            report['backend_differences']['first'] = dict(index=index,
                python=outputs['python'][index], native=outputs['native'][index])
    return report, frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', nargs='+', type=int, default=[7, 17, 27])
    parser.add_argument('--decisions', type=int, default=48)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--backends', nargs='+', choices=['python', 'native'], default=['python', 'native'])
    args = parser.parse_args()
    report, frames = benchmark(args.seeds, args.decisions, args.repetitions, args.warmup, args.backends,
                               progress=lambda message: print(message, file=sys.stderr, flush=True))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'headless.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    (args.output / 'frames.json').write_text(json.dumps(frames, allow_nan=False) + '\n')
    (args.output / 'legacy-replays.pkl').write_bytes(pickle.dumps(legacy_replays(args.seeds, args.decisions)))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
