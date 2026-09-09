"""Privileged dice experiments, with strict tapes and bounded GFI coupling.

Only already consumed rolls are exported by tape(); input tapes and cursors must
never be passed to policies. See docs/lab/chance.md for the experimental limits.
"""
from copy import deepcopy
from types import MappingProxyType

from botbowl.core.model import D3, D6, D8, BBDie, BBDieResult
from .randomness import SeedSpec, _canonical, _identifier


class ChanceError(ValueError):
    code = 'chance_divergence'


_DICE = {die.__name__: die for die in (D3, D6, D8, BBDie)}
# Ordered equiprobable physical faces, including the block die's repeated PUSH.
DOMAINS = MappingProxyType({'D3': (1, 2, 3), 'D6': (1, 2, 3, 4, 5, 6),
           'D8': (1, 2, 3, 4, 5, 6, 7, 8),
           'BBDie': tuple(BBDieResult(3 if n == 6 else n).name for n in range(1, 7))})
_MODES = ('independent', 'replay', 'forced', 'matched')


def _require(condition, message):
    if not condition:
        raise ChanceError(message)


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys.split()), 'Invalid chance fields')


def _nat(value):
    return type(value) is int and value >= 0


def _text(value):
    return type(value) is str and bool(value)


def _event(event):
    _keys(event, 'rule participants phase cause occurrence details matchable')
    _require(_text(event['rule']) and type(event['participants']) is dict and
             all(_text(k) and _text(v) for k, v in event['participants'].items()), 'Invalid rule/participants')
    _require(type(event['phase']) is list and len(event['phase']) == 5 and
             all(v is None or _nat(v) for v in event['phase']), 'Invalid logical phase')
    cause = event['cause']
    _keys(cause, 'decision event stack')
    _require((cause['decision'] is None or _nat(cause['decision'])) and _nat(cause['event']) and
             type(cause['stack']) is list and all(_text(v) for v in cause['stack']), 'Invalid causal occurrence')
    _require(_nat(event['occurrence']) and type(event['matchable']) is bool, 'Invalid occurrence')
    details = event['details']
    _keys(details, 'from to weather')
    for point in (details['from'], details['to']):
        _require(point is None or type(point) is list and len(point) == 2 and
                 all(_nat(v) for v in point), 'Invalid event position')
    _require(details['weather'] is None or _text(details['weather']), 'Invalid weather')
    if event['matchable']:
        _require(event['rule'] == 'GFI' and set(event['participants']) == {'player'} and
                 cause['decision'] is not None and cause['stack'] and cause['stack'][-1] == 'GFI' and
                 all(details[k] is not None for k in details), 'Unsupported matching site')


def _roll(row, index):
    _keys(row, 'index die domain context result mode provenance scope natural rng_advance')
    _require(type(row['index']) is int and row['index'] == index, 'Invalid tape order/index')
    _require(type(row['die']) is str and row['die'] in DOMAINS and
             type(row['domain']) is list and row['domain'] == list(DOMAINS[row['die']]) and
             all(type(x) is type(y) for x, y in zip(row['domain'], DOMAINS[row['die']])), 'Invalid die domain')
    _require(any(type(row['result']) is type(v) and row['result'] == v for v in row['domain']),
             'Invalid die result')
    _event(row['context'])
    _require(row['mode'] in _MODES and type(row['natural']) is bool and
             _text(row['provenance']) and row['scope'] in ('none', 'recorded-dice', 'declared-gfi'),
             'Invalid chance provenance')
    _require(row['mode'] != 'forced' or not row['natural'], 'Forced results are fabricated')
    _require(type(row['rng_advance']) is bool and
             (not row['rng_advance'] or row['natural']) and
             (row['scope'] != 'declared-gfi' or not row['rng_advance']), 'Invalid sampler provenance')


def validate_tape(tape):
    _keys(tape, 'version rolls')
    _require(type(tape['version']) is int and tape['version'] == 1 and type(tape['rolls']) is list,
             'Unsupported tape version')
    seen = set()
    for index, row in enumerate(tape['rolls']):
        _roll(row, index)
        key = _canonical(row['context'])
        _require(key not in seen, 'Repeated tape event')
        seen.add(key)
    return deepcopy(tape)


class ChancePolicy:
    """Owned experiment state. Install via EpisodeContext or install_chance().

    matches is a list of {source_index, target} declarations over a recorded
    tape. Only the closed GFI context is eligible. No draw-position alignment.
    """

    def __init__(self, mode='independent', *, tape=None, matches=(), unmatched='error', seed=None):
        _require(type(mode) is str and mode in _MODES, 'Unknown chance mode')
        _require(unmatched in ('error', 'independent'), 'Unknown unmatched behavior')
        _require(seed is None or type(seed) is SeedSpec and seed.purpose == 'engine', 'Expected engine SeedSpec')
        _require(mode in ('independent', 'matched') or seed is None, 'Replay/forced do not reseed the engine')
        self.mode = mode
        self._input = validate_tape({'version': 1, 'rolls': []} if tape is None else tape)
        self._matches = deepcopy(list(matches))
        _require(len(self._matches) <= 32, "At most 32 declared GFI pairs per comparison")
        self.unmatched = unmatched
        self.seed = seed
        self._cursor = 0
        self._used = []
        self._counts = {}
        self._rolls = []
        self._game = None
        self._fabricated = mode == "forced"
        _require(mode in ('replay', 'forced', 'matched') or tape is None, 'Independent mode has no input tape')
        _require(mode == 'matched' or not self._matches, 'Matches require matched mode')
        sources, targets = set(), set()
        for entry in self._matches:
            _keys(entry, 'source_index target')
            idx, target = entry['source_index'], entry['target']
            _require(_nat(idx) and idx < len(self._input['rolls']), 'Unknown matched source event')
            _event(target)
            source = self._input['rolls'][idx]
            _require(source['natural'] and source['die'] == 'D6' and source['context']['matchable'] and
                     target['matchable'], 'Only natural GFI D6 events can be matched')
            _require(all(source['context'][k] == target[k] for k in
                         ('rule', 'participants', 'phase', 'details')), 'Incompatible matched event semantics')
            key = _canonical(target)
            _require(idx not in sources and key not in targets, 'Ambiguous or repeated correspondence')
            sources.add(idx)
            targets.add(key)

    @property
    def cursor(self):
        return self._cursor

    def tape(self):
        return deepcopy({'version': 1, 'rolls': self._rolls})

    def metadata(self):
        fabricated = self._fabricated or self.mode == 'forced' or any(not r['natural'] for r in self._rolls) or (
            self.mode == 'replay' and any(not r['natural'] for r in self._input['rolls']))
        return {'mode': self.mode, 'provenance': 'fabricated' if fabricated else
                'recorded-dice' if self.mode == 'replay' else 'engine-MT19937',
                'scope': 'declared-gfi' if self.mode == 'matched' else
                'recorded-dice' if self.mode == 'replay' else 'none',
                'natural': not fabricated,
                'source': 'episode-engine' if self.seed is None else self.seed.to_json()}

    def finish(self):
        """Close a declared comparison prefix; reject leftover tape/matches."""
        if self.mode in ('replay', 'forced'):
            _require(self._cursor == len(self._input['rolls']), 'Unconsumed tape suffix')
        if self.mode == 'matched':
            _require(len(self._used) == len(self._matches), 'Unconsumed matched events')
        return self.metadata()

    def context(self):
        """Describe the NEXT die event without consuming a tape or random draw."""
        game = self._game
        _require(game is not None, 'Chance policy must be bound to a game')
        from .observations import ObservationControl
        from botbowl.core.procedure import GFI
        # Timeline owns the initial participant IDs for supported matching.
        # Rebinding from live roster order can alias a different player after
        # a reorder, or lose a valid match after snapshot restoration.
        control = (ObservationControl(game) if game.timeline is None else
                   game.timeline._entities)
        control._check(game)
        stack = list(game.state.stack.items)
        proc = stack[-1] if stack else None
        participants = {}
        for role in ('player', 'attacker', 'defender', 'catcher', 'passer', 'inflictor'):
            player = getattr(proc, role, None)
            if player is not None and hasattr(player, 'player_id'):
                participants[role] = control._player(player)
        team = getattr(proc, 'team', None)
        if team is not None and hasattr(team, 'team_id'):
            participants['team'] = control._team(team)
        ctx = None if game.timeline is None else game.timeline.context
        point = lambda p: None if p is None else [p.x, p.y]
        is_gfi = type(proc) is GFI
        event = {'rule': 'external' if proc is None else type(proc).__name__,
                 'participants': participants,
                 'phase': [game.state.half, game.state.round] +
                          ([None] * 3 if ctx is None else [ctx.drive_seq, ctx.team_turn_seq, ctx.activation_seq]),
                 'cause': {'decision': None if ctx is None else ctx.decision_seq,
                           'event': len(game.state.reports) if ctx is None else ctx.event_seq,
                           'stack': [type(p).__name__ for p in stack]},
                 'occurrence': 0,
                 'details': {'from': point(proc.player.position) if is_gfi else None,
                             'to': point(proc.position) if is_gfi else None,
                             'weather': game.state.weather.name if is_gfi else None},
                 'matchable': is_gfi and ctx is not None and proc.roll is None}
        key = _canonical(event)
        event['occurrence'] = self._counts.get(key, 0)
        _event(event)
        return event

    def roll(self, source, die):
        event = self.context()
        name = die.__name__
        expected = None
        matched_idx = None
        if self.mode in ('replay', 'forced'):
            _require(self._cursor < len(self._input['rolls']), 'Chance tape exhausted')
            expected = self._input['rolls'][self._cursor]
            _require(expected['context'] == event, 'Chance event diverged')
        elif self.mode == 'matched':
            key = _canonical(event)
            found = [m for m in self._matches if _canonical(m['target']) == key]
            if found:
                matched_idx = found[0]['source_index']
                _require(matched_idx not in self._used, 'Matched event already consumed')
                expected = self._input['rolls'][matched_idx]
            else:
                # A shifted occurrence at a declared site is a divergence even
                # when unrelated unmatched sites may use independent draws.
                site = lambda e: (e['rule'], e['participants'], e['phase'], e['details'])
                _require(not any(site(m['target']) == site(event) for m in self._matches),
                         'Declared matched event diverged or repeated')
                _require(self.unmatched == 'independent', 'Unmatched chance event')
        if expected is not None:
            _require(expected['die'] == name and expected['domain'] == list(DOMAINS[name]),
                     'Chance die/domain diverged')
            # Mixing legacy test queues with replay/coupling would hide provenance.
            _require(not any(any(frame.values()) for frame in source._queues) and
                     not any(source._strict), 'Forced queues conflict with tape consumption')
            value = expected['result']
            value = BBDieResult[value] if die is BBDie else value
            natural = self.mode != 'forced' and expected['natural']
            rng_advance = self.mode == 'replay' and expected['rng_advance']
            if rng_advance:
                # Preserve the original interleaving with direct Game.rng uses.
                # The recorded result, not this discarded draw, is authoritative.
                die(source.rng)
            provenance = ('matched:' + str(matched_idx) if self.mode == 'matched' else
                          self.mode + ':' + str(self._cursor)) + ':' + expected['provenance']
        else:
            natural = not bool(source._queues[-1][die])
            rng_advance = natural
            value = source._roll_unmanaged(die)
            provenance = 'independent-fallback' if self.mode == 'matched' else 'engine-MT19937'
            if not natural:
                provenance = 'legacy-forced-queue'
        self._fabricated = self._fabricated or not natural
        row = {'index': len(self._rolls), 'die': name, 'domain': list(DOMAINS[name]),
               'context': event, 'result': value.name if die is BBDie else int(value),
               'mode': self.mode if natural else 'forced', 'provenance': provenance,
               'rng_advance': rng_advance,
               'scope': 'declared-gfi' if matched_idx is not None else
                        'recorded-dice' if self.mode == 'replay' else 'none', 'natural': natural}
        self._rolls.append(row)
        base = dict(event, occurrence=0)
        self._counts[_canonical(base)] = event['occurrence'] + 1
        if self.mode in ('replay', 'forced'):
            self._cursor += 1
        if matched_idx is not None:
            self._used.append(matched_idx)
        return value

    def to_json(self):
        """Privileged state adapter, including future inputs and consumed cursor."""
        return deepcopy({'version': 1, 'mode': self.mode, 'input': self._input,
                         'matches': self._matches, 'unmatched': self.unmatched,
                         'seed': None if self.seed is None else self.seed.to_json(),
                         'cursor': self._cursor, 'used': self._used, 'counts': self._counts,
                         'output': self.tape(), 'fabricated': self._fabricated})

    @classmethod
    def from_json(cls, state):
        _keys(state, 'version mode input matches unmatched seed cursor used counts output fabricated')
        _require(type(state['version']) is int and state['version'] == 1, 'Unsupported chance state version')
        seed = None if state['seed'] is None else SeedSpec(**state['seed'])
        result = cls(state['mode'], tape=None if state['mode'] == 'independent' else state['input'],
                     matches=state['matches'], unmatched=state['unmatched'], seed=seed)
        _require(state['mode'] != 'independent' or state['input'] == {'version': 1, 'rolls': []},
                 'Independent state contains input tape')
        output = validate_tape(state['output'])['rolls']
        _require(type(state['fabricated']) is bool and
                 (state['fabricated'] or result.mode != 'forced' and all(r['natural'] for r in output)),
                 'Invalid fabricated provenance')
        result._fabricated = state['fabricated']
        _require(_nat(state['cursor']) and state['cursor'] ==
                 (len(output) if result.mode in ('replay', 'forced') else 0) and
                 state['cursor'] <= len(result._input['rolls']), 'Invalid tape cursor')
        used = state['used']
        _require(type(used) is list and all(_nat(v) for v in used) and len(set(used)) == len(used) and
                 set(used) <= {m['source_index'] for m in result._matches}, 'Invalid matched cursor')
        counts = {}
        for row in output:
            event = row['context']
            key = _canonical(dict(event, occurrence=0))
            _require(event['occurrence'] == counts.get(key, 0), 'Invalid causal occurrence count')
            counts[key] = event['occurrence'] + 1
        _require(type(state['counts']) is dict and state['counts'] == counts and
                 all(_nat(v) for v in state['counts'].values()), 'Invalid event counters')
        if result.mode in ('replay', 'forced'):
            for row, original in zip(output, result._input['rolls']):
                _require(all(row[k] == original[k] for k in ('die', 'domain', 'context', 'result')) and
                         row['natural'] == (result.mode != 'forced' and original['natural']) and
                         row['rng_advance'] == (result.mode == 'replay' and original['rng_advance']),
                         'Consumed replay prefix diverges from input')
        matched_used = []
        for row in output:
            if row['scope'] == 'declared-gfi':
                candidates = [m for m in result._matches if m['target'] == row['context']]
                _require(result.mode == 'matched' and len(candidates) == 1, 'Undeclared consumed match')
                idx = candidates[0]['source_index']
                original = result._input['rolls'][idx]
                _require(row['natural'] and all(row[k] == original[k] for k in ('die', 'domain', 'result')),
                         'Consumed match differs from source')
                matched_used.append(idx)
        _require(used == matched_used, 'Matched cursor differs from consumed events')
        result._cursor, result._used, result._counts, result._rolls = (
            state['cursor'], list(used), counts, output)
        return result


def install_chance(game, policy=None):
    """Copy and explicitly bind a policy; an optional seed starts a new substream.

    A snapshot clone otherwise continues the captured stream exactly. Use a
    distinct engine SeedSpec for each independent continuation, before advancing.
    """
    policy = ChancePolicy() if policy is None else policy
    _require(type(policy) is ChancePolicy, 'Expected ChancePolicy')
    owned = ChancePolicy.from_json(policy.to_json())
    owned._game = game
    previous = game.dice.chance
    owned._fabricated = owned._fabricated or (previous is not None and not previous.metadata()["natural"])
    if owned.seed is not None:
        _require(not owned._rolls, 'Cannot reseed advanced chance state')
        game.rng.set_state(owned.seed.generator().get_state())
    game.dice.chance = owned
    return owned


def branch_from_snapshot(snapshot, *, branch_id, policy=None):
    """Clone a privileged snapshot and select continuation semantics explicitly.

    Default independent continuations choose and record fresh OS entropy. Supply
    an engine SeedSpec on the policy for scheduling-independent reproducibility.
    A bare snapshot clone still means exact restoration, never fresh chance.
    """
    from .snapshots import clone_from_snapshot
    _identifier(branch_id)
    branch = clone_from_snapshot(snapshot)
    game = branch.game if hasattr(branch, 'game') else branch
    if game.timeline is not None:
        game.timeline.fork(branch_id)
    if policy is None:
        policy = ChancePolicy(seed=SeedSpec(episode_key=game.game_id, component_id=branch_id))
    elif policy.mode in ('independent', 'matched') and policy.seed is None:
        state = policy.to_json()
        state['seed'] = SeedSpec(episode_key=game.game_id, component_id=branch_id).to_json()
        policy = ChancePolicy.from_json(state)
    installed = install_chance(game, policy)
    if branch is not game:
        branch._manifest['chance'] = installed.metadata()
    return branch
