"""Sequential, data-only headless dataset jobs and supplied-action replay.

Run ``python -m botbowl.lab.generate --help``. See docs/lab/generate.md.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from .actions import ActionV1
from .channels import PRIMARY_PROFILE, project_inputs
from .randomness import DERIVATION_ALGORITHM, SeedSpec
from .recording import EpisodeReader
from .records import decode_json, encode_json
from .scenarios import SCENARIO_RECIPES, ScenarioSpecV1, create_scenario, scenario_spec
from .session import NoProgress, SessionConfig, SimulationSession


VERSION = 1
POLICIES = ('random', 'scripted')


def _hash(data):
    return hashlib.sha256(encode_json(data)).hexdigest()


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('%s must be an integer in [%d, %d]' % (name, low, high))


@dataclass(frozen=True)
class JobConfig:
    output: str
    episodes: int = 1
    master_seed: int = 0
    scenario: str = 'movement'
    size: int = 3
    side: str = 'home'
    home_policy: str = 'scripted'
    away_policy: str = 'random'
    input_profile: str = 'primary'
    max_decisions: int = 32
    max_steps: int = 1000
    horizon: int = None
    distance: int = 1
    rerolls: int = 0

    def __post_init__(self):
        _integer(self.episodes, 1, 1000, 'episodes')
        _integer(self.master_seed, 0, 2**256 - 1, 'master_seed')
        _integer(self.max_decisions, 0, 10000 if self.scenario == 'match' else 256, 'max_decisions')
        _integer(self.max_steps, 1, 10000, 'max_steps')
        _integer(self.distance, 1, 3, 'distance')
        _integer(self.rerolls, 0, 3, 'rerolls')
        if self.horizon is not None:
            _integer(self.horizon, 0, self.max_decisions, 'horizon')
        if type(self.size) is not int or self.size not in (1, 3, 5, 7, 11):
            raise ValueError('Unsupported board size')
        if self.side not in ('home', 'away') or type(self.side) is not str:
            raise ValueError('side must be home or away')
        if type(self.scenario) is not str or self.scenario not in ('match', *SCENARIO_RECIPES):
            raise ValueError('Unknown scenario')
        if any(type(p) is not str or p not in POLICIES for p in (self.home_policy, self.away_policy)):
            raise ValueError('Unknown policy ID')
        if self.input_profile != 'primary':
            raise ValueError('This generator supports the authorized primary profile only')
        if type(self.output) is not str or not self.output.strip():
            raise ValueError('output must be an explicit directory')
        if self.scenario == 'match':
            if self.distance != 1 or self.rerolls != 0 or self.side != 'home':
                raise ValueError('Match jobs do not accept synthetic scenario parameters')
        else:
            scenario_spec(self.scenario, size=self.size, side=self.side,
                          parameters={'distance': self.distance, 'rerolls': self.rerolls},
                          max_decisions=max(1, self.max_decisions), max_steps=self.max_steps)


class ReferencePolicy:
    """An owned private RNG and closed policy ID; no Game or seed in act inputs."""

    def __init__(self, policy_id, seed):
        if policy_id not in POLICIES:
            raise ValueError('Unknown policy ID')
        self.policy_id = policy_id
        self.rng = seed.generator()
        self.closed = False
        self._last_type = None

    def act(self, features, legal):
        if self.closed or not legal.actions:
            raise ValueError('Policy has no live legal decision')
        if self.policy_id == 'random':
            return legal.actions[int(self.rng.randint(len(legal.actions)))]
        # END_SETUP is an accepted no-op when placement is incomplete. Install
        # the shipped formation first on each setup, then submit END_SETUP.
        if self._last_type not in ('SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE'):
            for action in legal.actions:
                if action.type in ('SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE'):
                    self._last_type = action.type
                    return action
        # A bounded lifecycle smoke script. It claims no tactical strength.
        for name in ('START_GAME', 'HEADS', 'RECEIVE', 'END_SETUP',
                     'SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE', 'END_TURN'):
            for action in legal.actions:
                if action.type == name:
                    self._last_type = action.type
                    return action
        self._last_type = legal.actions[0].type
        return legal.actions[0]

    def close(self):
        self.closed = True


def build_plan(config):
    """Validate the entire job and materialize all seeds before any execution."""
    if type(config) is not JobConfig:
        raise ValueError('Expected JobConfig')
    config.__post_init__()
    job = asdict(config)
    del job['output']
    entries = []
    for index in range(config.episodes):
        episode_id = 'episode-%06d' % index
        components = {'scenario': 'recipes-v1', 'engine': 'generator-v1',
                      'policy-home': config.home_policy + '-v1',
                      'policy-away': config.away_policy + '-v1', 'observation': 'primary-v1'}
        sources = {purpose: SeedSpec(config.master_seed, episode_id, purpose, component).to_json()
                   for purpose, component in components.items()}
        if config.scenario == 'match':
            from .scenarios import _resources
            rules = _resources(config.size)[3].to_json()
            recipe = {'kind': 'match', 'config': 'gym-%d' % config.size, 'rules': rules}
        else:
            # The registered scenario constructor uses a scenario-purpose stream
            # derived from its own seed. Retain both derivation levels exactly.
            words = SeedSpec(**sources['scenario']).seed_words()
            scenario_seed = sum(word << (32 * (7 - i)) for i, word in enumerate(words))
            spec = scenario_spec(config.scenario, size=config.size, side=config.side,
                                 scenario_seed=scenario_seed,
                                 parameters={'distance': config.distance, 'rerolls': config.rerolls},
                                 max_decisions=max(1, config.max_decisions), max_steps=config.max_steps)
            recipe = {'kind': 'scenario', 'spec': spec.to_json()}
            sources['scenario-layout'] = SeedSpec(scenario_seed, config.scenario,
                                                  'scenario', 'recipes-v1').to_json()
        origin = 'origin-' + _hash({'recipe': recipe, 'engine': sources['engine']})
        entries.append({'episode_id': episode_id, 'origin_family_id': origin, 'recipe': recipe,
                        'policies': {side: {'id': getattr(config, side + '_policy'), 'version': '1'}
                                     for side in ('home', 'away')},
                        'seed_plan': {'schema_version': 1, 'algorithm': DERIVATION_ALGORITHM,
                                      'sources': sources},
                        'start': 'match_start' if config.scenario == 'match' else 'synthetic_turn',
                        'horizon': config.horizon, 'decision_budget': config.max_decisions})
    return {'schema_version': VERSION, 'generator': 'botbowl.lab.generate-v1',
            'job': job, 'profile': PRIMARY_PROFILE.to_json(), 'episodes': entries}


def _validate_plan(plan):
    if type(plan) is not dict or type(plan.get('job')) is not dict:
        raise ValueError('Invalid generation plan')
    try:
        config = JobConfig(output='unused', **plan['job'])
        expected = build_plan(config)
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid generation plan') from error
    if encode_json(plan) != encode_json(expected):
        raise ValueError('Plan does not match the supported recipe, policy, seeds or loaded rules/backend')
    return config


def _open_session(entry, config):
    seed = SeedSpec(**entry['seed_plan']['sources']['engine'])
    if entry['recipe']['kind'] == 'scenario':
        return create_scenario(ScenarioSpecV1.from_json(entry['recipe']['spec']), seed_plan=seed)
    from .scenarios import _resources
    game_config, home, away, _ = _resources(config.size)
    return SimulationSession(SessionConfig(game_config, home, away, config.size,
                                            config.max_decisions, config.max_steps), seed)


def _end(result, count, entry):
    if result.terminated:
        reason = 'game_over'
    elif getattr(result, 'scenario_terminal', False):
        reason = 'scenario_terminal'
    elif result.truncated:
        reason = result.end_reason
    elif count >= entry['decision_budget']:
        reason = 'decision_budget'
    elif entry['horizon'] is not None and count >= entry['horizon']:
        reason = 'fragment_horizon'
    else:
        return None
    return {'reason': reason, 'terminated': result.terminated,
            'truncated': reason not in ('game_over', 'scenario_terminal'),
            'scenario_terminal': getattr(result, 'scenario_terminal', False),
            'scenario_success': getattr(result, 'scenario_success', None), 'decisions': count}


def _atomic_json(path, data):
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('xb') as stream:
        stream.write(encode_json(data))
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.rename(path)


def _run(entry, config, destination, supplied=None):
    with ExitStack() as resources:
        session = _open_session(entry, config)
        resources.callback(session.close)
        recorder = session.start_recording(destination, entry['episode_id'],
            episode_id=entry['episode_id'], source_family=entry['origin_family_id'],
            scenario_id=config.scenario, policies=entry['policies'], seed_plan=entry['seed_plan'])
        resources.callback(recorder.close)
        policies = {}
        if supplied is None:
            for side in ('home', 'away'):
                policy = ReferencePolicy(entry['policies'][side]['id'],
                    SeedSpec(**entry['seed_plan']['sources']['policy-' + side]))
                resources.callback(policy.close)
                policies[side] = policy
        result = session.observe()
        count = 0
        while _end(result, count, entry) is None:
            legal = session.legal_actions()
            if supplied is not None:
                if count >= len(supplied):
                    raise ValueError('Supplied actions ended before the declared episode boundary')
                action = supplied[count]
            else:
                features = project_inputs({'primary': result.primary}, PRIMARY_PROFILE)['features']
                action = policies[result.next_actor].act(features, deepcopy(legal))
            try:
                result = session.step(action, legal.state_revision)
                count += 1
            except NoProgress:
                # The session records the admitted, possibly pending decision;
                # budget exhaustion is a partial continuation, never a defeat.
                count += 1
                result = session.observe()
        end = _end(result, count, entry)
        if supplied is not None and count != len(supplied):
            raise ValueError('Supplied actions continue past the declared episode boundary')
        recorder.append_channel('privileged', 1, {'generation': {
            'origin_family_id': entry['origin_family_id'], 'start': entry['start'],
            'horizon': entry['horizon'], 'decision_budget': entry['decision_budget'], 'end': end}})
        manifest = recorder.finish(truncation_reason=None if result.terminated else end['reason']).to_json()
        episode = EpisodeReader(destination, entry['episode_id']).read_episode()
        return {'episode_id': entry['episode_id'], 'origin_family_id': entry['origin_family_id'],
                'end': end, 'semantic_sha256': _hash(episode),
                'manifest_sha256': _hash(manifest)}


def execute_plan(plan, output, *, _replay=None):
    """Execute a validated plan sequentially into a new directory; never resume."""
    config = _validate_plan(plan)
    destination = Path(output).resolve()
    package = Path(__file__).resolve().parents[1]
    if destination == package or package in destination.parents:
        raise ValueError('Datasets cannot be written inside the installed package')
    destination.mkdir(parents=True, exist_ok=False)
    _atomic_json(destination / 'plan.json', plan)
    results = []
    for entry in plan['episodes']:
        if _replay is not None and entry['episode_id'] != _replay[0]:
            continue
        try:
            results.append(_run(entry, config, destination,
                                None if _replay is None else _replay[1]))
        except BaseException as error:
            # Stable diagnostics exclude arbitrary exception text, rejected
            # payloads, tracebacks, paths, credentials and incidental timestamps.
            diagnostic = {'schema_version': 1, 'episode_id': entry['episode_id'],
                          'error_type': type(error).__name__, 'status': 'failed'}
            try:
                _atomic_json(destination / (entry['episode_id'] + '.failure.json'), diagnostic)
            except OSError:
                pass  # Preserve the original failure and visible partial writer.
            raise
    dataset = {'schema_version': VERSION, 'plan_sha256': _hash(plan),
               'replay_episode_id': None if _replay is None else _replay[0], 'episodes': results}
    _atomic_json(destination / 'dataset.json', dataset)
    return dataset


def generate(config):
    return execute_plan(build_plan(config), config.output)


def validate_dataset(destination):
    """Fully validate DATA-02 channels and their job-level semantic identities."""
    try:
        return _validate_dataset(destination)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError('Malformed dataset metadata') from error


def _validate_dataset(destination):
    root = Path(destination)
    plan = decode_json((root / 'plan.json').read_bytes())
    config = _validate_plan(plan)
    dataset = decode_json((root / 'dataset.json').read_bytes())
    if (set(dataset) != {'schema_version', 'plan_sha256', 'replay_episode_id', 'episodes'} or
            type(dataset['schema_version']) is not int or dataset['schema_version'] != VERSION or
            type(dataset['episodes']) is not list or dataset['plan_sha256'] != _hash(plan)):
        raise ValueError('Dataset plan identity mismatch')
    entries = {entry['episode_id']: entry for entry in plan['episodes']}
    seen = set()
    for summary in dataset['episodes']:
        episode_id = summary['episode_id']
        if episode_id not in entries or episode_id in seen:
            raise ValueError('Unknown or duplicate episode')
        seen.add(episode_id)
        episode = EpisodeReader(root, episode_id).read_episode()
        entry = entries[episode_id]
        manifest = episode['manifest']
        rules = entry['recipe']['rules'] if entry['recipe']['kind'] == 'match' else entry['recipe']['spec']['rules']
        generation = episode['channels']['privileged'][0]['channel']['data']['generation']
        if (_hash(episode) != summary['semantic_sha256'] or
                _hash(manifest) != summary['manifest_sha256'] or
                manifest['episode_id'] != episode_id or manifest['profile'] != plan['profile'] or
                manifest['source_family'] != entry['origin_family_id'] or
                summary['origin_family_id'] != entry['origin_family_id'] or
                manifest['scenario_id'] != config.scenario or
                manifest['provenance']['rules'] != rules or
                manifest['provenance']['policies'] != entry['policies'] or
                manifest['provenance']['seed_plan'] != entry['seed_plan'] or
                generation != {'origin_family_id': entry['origin_family_id'],
                    'start': entry['start'], 'horizon': entry['horizon'],
                    'decision_budget': entry['decision_budget'], 'end': summary['end']} or
                manifest['end']['reason'] != summary['end']['reason'] or
                len(episode['channels']['transitions']) != summary['end']['decisions']):
            raise ValueError('Dataset episode identity/outcome mismatch')
        EpisodeReader(root, episode_id).read_inputs()
    expected_ids = set(entries) if dataset['replay_episode_id'] is None else {dataset['replay_episode_id']}
    if seen != expected_ids:
        raise ValueError('Dataset is missing declared episodes')
    return dataset


def replay_episode(destination, episode_id, output, actions=None):
    """Rebuild from the plan and exact semantic actions, without calling policies."""
    dataset = validate_dataset(destination)
    if episode_id not in {row['episode_id'] for row in dataset['episodes']}:
        raise ValueError('Episode is not confirmed in this dataset')
    root = Path(destination)
    plan = decode_json((root / 'plan.json').read_bytes())
    if actions is None:
        rows = EpisodeReader(root, episode_id).read_channels(['transitions'])['transitions']
        actions = [row['action'] for row in rows]
    if type(actions) is not list:
        raise ValueError('Supplied actions must be a JSON array or JSONL action rows')
    actions = [ActionV1.from_json(action).to_json() for action in actions]
    return execute_plan(plan, output, _replay=(episode_id, deepcopy(actions)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('generate', help='Generate a new dataset')
    run.add_argument('--output', required=True)
    for name, default in (('episodes', 1), ('master-seed', 0), ('size', 3),
                          ('max-decisions', 32), ('max-steps', 1000), ('distance', 1), ('rerolls', 0)):
        run.add_argument('--' + name, type=int, default=default)
    run.add_argument('--scenario', choices=('match', *SCENARIO_RECIPES), default='movement')
    run.add_argument('--side', choices=('home', 'away'), default='home')
    run.add_argument('--home-policy', choices=POLICIES, default='scripted')
    run.add_argument('--away-policy', choices=POLICIES, default='random')
    run.add_argument('--input-profile', choices=('primary',), default='primary')
    run.add_argument('--horizon', type=int)
    check = commands.add_parser('validate', help='Validate every stored episode and its inputs')
    check.add_argument('dataset')
    replay = commands.add_parser('replay', help='Replay one episode from supplied or stored actions')
    replay.add_argument('dataset')
    replay.add_argument('episode_id')
    replay.add_argument('--output', required=True)
    replay.add_argument('--actions', help='JSONL file containing one ActionV1 per line')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'generate':
            result = generate(JobConfig(**args))
        elif command == 'validate':
            result = validate_dataset(args['dataset'])
        else:
            actions = None
            if args['actions']:
                with Path(args['actions']).open('rb') as stream:
                    actions = [decode_json(line) for line in stream]
            result = replay_episode(args['dataset'], args['episode_id'], args['output'], actions)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, 'Generation failed: %s\n' % type(error).__name__)


if __name__ == '__main__':
    main()
