"""Issue #25's bounded, deterministic probes (test support, not a game API)."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
from numbers import Integral
import random
import signal
import time

import botbowl as bb
from botbowl.core.procedure import Setup, StartGame
from tests.baseline import progress_action, semantic_reports


DICE = (bb.D3, bb.D6, bb.D8, bb.BBDie)
RESOURCES = ('rerolls', 'rerolls_start', 'apothecaries', 'bribes', 'babes',
             'score', 'turn', 'ass_coaches', 'cheerleaders', 'time_violation')


def action_record(action):
    return None if action is None else action.to_json()


def decode_action(game, record):
    if record is None:
        return None
    position = record['position']
    return bb.Action(bb.ActionType[record['action_type']],
                     player=None if record['player_id'] is None else game.state.player_by_id[record['player_id']],
                     position=None if position is None else game.get_square(position['x'], position['y']))


def candidates(choice):
    """Expand one advertised choice, never cross targets from different choices."""
    for player in choice.players or [None]:
        for position in choice.positions or [None]:
            yield bb.Action(choice.action_type, player=player, position=position)


def assert_invariants(game):
    """Check decision boundaries, including secondary actors and airborne players.

    A crowd square is an allocated board cell; a throw-team-mate passenger is
    above the board, not a second occupant. Ball.on_ground is not the inverse of
    is_carried in this engine. Signed stat modifiers are deliberately excluded
    from the nonnegative resource domain.
    """
    state = game.state
    teams = state.teams
    assert len(teams) == 2 and teams[0] is not teams[1], 'team identities'
    assert set(state.team_by_id) == {t.team_id for t in teams}, 'team index keys'
    players = [p for team in teams for p in team.players]
    ids = [p.player_id for p in players]
    assert len(ids) == len(set(ids)), 'duplicate roster player'
    assert set(state.player_by_id) == set(ids), 'player index keys'
    assert set(state.team_by_player_id) == set(ids), 'membership index keys'
    compartments = []
    for team in teams:
        assert state.team_by_id[team.team_id] is team, 'canonical team'
        for name in RESOURCES:
            value = getattr(team.state, name)
            assert isinstance(value, Integral) and not isinstance(value, bool) and value >= 0, (
                'resource', team.team_id, name, value)
        dugout = state.dugouts[team.team_id]
        assert dugout.team is team, 'dugout team'
        for name in ('reserves', 'kod', 'casualties', 'dungeon'):
            for player in getattr(dugout, name):
                assert player.team is team and player in team.players, 'foreign dugout player'
                assert player.position is None, ('pitch/dugout overlap', player.player_id, name)
                compartments.append(player.player_id)
        for player in team.players:
            assert player.team is team, ('roster team', player.player_id)
            assert state.player_by_id[player.player_id] is player, 'canonical player'
            assert state.team_by_player_id[player.player_id] is team, 'canonical membership'
            for name in ('moves', 'spp_earned'):
                value = getattr(player.state, name)
                assert isinstance(value, Integral) and value >= 0, ('player resource', name, value)
    assert len(compartments) == len(set(compartments)), 'duplicate dugout membership'
    occupied = []
    for y, row in enumerate(state.pitch.board):
        for x, player in enumerate(row):
            if player is not None:
                assert state.player_by_id.get(player.player_id) is player, 'unregistered board player'
                assert not player.state.in_air, 'airborne board occupant'
                assert player.position == game.get_square(x, y), ('board/position', player.player_id, x, y)
                occupied.append(player.player_id)
    assert len(occupied) == len(set(occupied)), 'duplicate occupancy'
    for player in players:
        if player.position is not None:
            pos = player.position
            assert 0 <= pos.x < game.arena.width and 0 <= pos.y < game.arena.height, 'player bounds'
            if not player.state.in_air:
                assert game.get_player_at(pos) is player, ('position/board', player.player_id)
        elif not any(isinstance(proc, StartGame) for proc in state.stack.items):
            assert player.player_id in compartments, ('missing player compartment', player.player_id)
    assert len(state.pitch.balls) <= 1, 'multiple balls in BB2016 corpus'
    for ball in state.pitch.balls:
        pos = ball.position
        if pos is not None:
            assert 0 <= pos.x < game.arena.width and 0 <= pos.y < game.arena.height, 'ball bounds'
        if ball.is_carried:
            carriers = [p for p in players if pos is not None and p.position == pos]
            assert len(carriers) == 1, 'carried ball without unique carrier'
        else:
            assert pos is not None, 'unplaced live ball'
    for team in (state.current_team, state.kicking_this_drive, state.receiving_this_drive):
        assert team is None or any(team is t for t in teams), 'foreign phase team'
    if state.active_player is not None:
        assert state.player_by_id.get(state.active_player.player_id) is state.active_player, 'active player'
        assert state.active_player.team is state.current_team, 'active player team'
    choices = game.get_available_actions()
    if state.game_over:
        assert not choices and game.actor is None, 'terminal decision'
        return
    assert choices, 'no decision before terminal'
    enabled = [c for c in choices if not c.disabled]
    assert enabled, 'no enabled choice'
    for choice in enabled:
        assert any(choice.team is t for t in teams), 'choice team'
        assert game.actor is game.get_team_agent(choice.team), 'actor/options disagree'
        for player in choice.players:
            assert state.player_by_id.get(player.player_id) is player, 'choice player'
            assert player.team is choice.team, 'choice player team'
        for action in candidates(choice):
            result = game.validate_action(action)
            assert result.allowed, ('advertised illegal action', action_record(action), result.code)


def fingerprint(game):
    # Reports, elapsed time and RNG churn cannot manufacture state progress.
    data = game.state.to_json(ignore_reports=True, ignore_clocks=True)
    data['procedures'] = game.get_procedure_names()
    data['used_skills'] = [[p.player_id, sorted(s.name for s in p.state.used_skills)]
                           for t in game.state.teams for p in t.players]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class CorpusFailure(AssertionError):
    def __init__(self, reason, reproduction):
        self.reproduction = dict(reproduction, failure=str(reason))
        super().__init__(json.dumps(self.reproduction, sort_keys=True))


@dataclass
class Probe:
    game: bb.Game
    config: dict
    max_decisions: int = 1500
    engine_steps: int = 2000
    # Legal START/UNDO cycles revisit a turn. Sixteen visits bound a stalled
    # policy without treating a handful of legal activation cancellations as a
    # product defect. This limit applies identically to every seed and size.
    repeat_limit: int = 16
    seconds: float = 90
    actions: list = field(default_factory=list)
    forced: list = field(default_factory=list)
    fixtures: list = field(default_factory=list)
    visits: Counter = field(default_factory=Counter)
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        for name in ('max_decisions', 'repeat_limit', 'engine_steps'):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == 'engine_steps' else 1):
                raise ValueError('invalid ' + name)
        validate_seconds(self.seconds)

    def reproduction(self):
        record = dict(config=self.config, actions=self.actions[:], forced=self.forced[:], fixtures=self.fixtures[:],
                      decision=len(self.actions), procedures=self.game.get_procedure_names(),
                      options=[c.to_json() for c in self.game.get_available_actions()],
                      limits=dict(decisions=self.max_decisions, engine_steps=self.engine_steps,
                                  repeats=self.repeat_limit, seconds=self.seconds))
        # Pathfinding rolls contain tuples. Use the persisted JSON representation
        # for the entire journal, including records replayed directly in memory.
        return json.loads(json.dumps(record))

    @contextmanager
    def evidence(self):
        """Give scenario expectations and synthetic mutations the same diagnostics."""
        try:
            remaining = self.seconds - (time.monotonic() - self.started)
            assert remaining > 0, 'wall-time limit'
            with deadline(remaining):
                yield
        except CorpusFailure:
            raise
        except Exception as error:
            raise CorpusFailure(error, self.reproduction()) from error

    def fixture(self, description):
        self.fixtures.append(dict(decision=len(self.actions), description=description,
                                  state=self.game.state.to_json(ignore_reports=True, ignore_clocks=True)))
        self.check()

    def check(self):
        try:
            assert time.monotonic() - self.started < self.seconds, 'wall-time limit'
            before = self.game.capture_rng_state()
            assert_invariants(self.game)
            assert self.game.capture_rng_state() == before, 'invariant checker consumed RNG/queues'
            key = fingerprint(self.game)
            self.visits[key] += 1
            assert self.visits[key] <= self.repeat_limit, 'nonprogress: repeated semantic state'
        except CorpusFailure:
            raise
        except Exception as error:
            raise CorpusFailure(error, self.reproduction()) from error

    def step(self, action):
        try:
            remaining = self.seconds - (time.monotonic() - self.started)
            assert remaining > 0, 'wall-time limit'
            assert len(self.actions) < self.max_decisions, 'decision limit before termination'
            assert self.game.validate_action(action).allowed, ('selected illegal action', action_record(action))
            self.actions.append(action_record(action))
            with deadline(remaining):
                self.game.advance(action, max_steps=self.engine_steps)
            self.check()
        except CorpusFailure:
            raise
        except Exception as error:
            raise CorpusFailure(error, self.reproduction()) from error

    @contextmanager
    def dice(self, **rolls):
        self.forced.append(dict(decision=len(self.actions), rolls={
            key: [v.name if isinstance(v, bb.BBDieResult) else v for v in values]
            for key, values in rolls.items()}))
        before = self.game.capture_rng_state()
        try:
            with self.game.dice.force(strict=True, **rolls):
                yield
                assert all(not self.game.dice.pending(die) for die in DICE), 'unconsumed forced rolls'
                assert self.game.capture_rng_state().rng_state == before.rng_state, 'unforced randomness'
            assert self.game.capture_rng_state() == before, 'forced scope leaked RNG/queues'
        except CorpusFailure:
            raise
        except Exception as error:
            raise CorpusFailure(error, self.reproduction()) from error

    def result(self):
        events = semantic_reports(self.game)
        payload = dict(actions=self.actions, events=events, state=fingerprint(self.game),
                       rng=repr(self.game.capture_rng_state()))
        return dict(config=self.config, decisions=len(self.actions), terminal=self.game.state.game_over,
                    digest=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                    actors=sorted({c['player_id'].split('-')[0] for c in self.actions
                                   if c is not None and c['player_id'] is not None}),
                    events=dict(Counter(e['event'] for e in events)))


def fresh(size=3, seed=0, pathfinding=False, turns=1, home='human', away='human', ogre=False, **metadata):
    config = bb.load_config('gym-' + str(size))
    config.rounds = turns
    config.pathfinding_enabled = pathfinding
    config.pathfinding_directly_to_adjacent = False
    rules = bb.load_rule_set(config.ruleset)
    teams = [bb.load_team_by_filename(name, rules, board_size=size) for name in (home, away)]
    if ogre:
        # A legal Human roster variant: replace one lineman by the BB2016 Ogre
        # role before constructing Game (the shipped sample has no Ogre).
        for team in teams:
            team.players[-1].role = rules.get_role('Ogre', 'Human')
    for side, team in zip(('home', 'away'), teams):
        team.team_id = side
        for i, player in enumerate(team.players):
            player.player_id = side + '-' + str(i)
    game = bb.Game('issue25', *teams, bb.Agent('home', human=True, agent_id='home'),
                   bb.Agent('away', human=True, agent_id='away'), config, seed=seed, external_control=True)
    from botbowl.core import pathfinding as pf
    probe = Probe(game, dict(size=size, seed=seed, policy_seed=seed + 1009, pathfinding=pathfinding,
                             backend=pf.get_safest_path.__module__, turns=turns, home=home, away=away,
                             kick_off_table=config.kick_off_table, ruleset=config.ruleset, ogre=ogre, **metadata))
    with probe.evidence():
        game.init(max_steps=probe.engine_steps)
        probe.check()
    return probe


class LegalSequence:
    """A local policy RNG; setup uses formations, other enabled choices are sampled.

    No product-failure exclusions or retries. Sampling a choice before its
    targets avoids bias towards choices with many board squares.
    """
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def action(self, game):
        if isinstance(game.get_procedure(), Setup):
            return progress_action(game)
        choices = [c for c in game.get_available_actions() if not c.disabled]
        choice = self.rng.choice(choices)
        return bb.Action(choice.action_type,
                         player=self.rng.choice(choice.players) if choice.players else None,
                         position=self.rng.choice(choice.positions) if choice.positions else None)


def generated(size=3, seed=0, pathfinding=False, turns=1, home='human', away='human', interleave=None):
    probe = fresh(size, seed, pathfinding, turns, home, away, case='generated', origin='natural')
    for _ in sequence(probe):
        if interleave is not None:
            state = probe.game.capture_rng_state()
            interleave()
            assert probe.game.capture_rng_state() == state, 'another game changed RNG/queues'
    return probe


def sequence(probe):
    policy = LegalSequence(probe.config['policy_seed'])
    while not probe.game.state.game_over:
        before = probe.game.capture_rng_state()
        action = policy.action(probe.game)
        assert probe.game.capture_rng_state() == before, 'policy consumed game RNG/queues'
        probe.step(action)
        yield probe
    assert all(not probe.game.dice.pending(die) for die in DICE)


def replay(record):
    """Replay natural actions, or verify a named synthetic recipe's full journal.

    Synthetic placements are not legal decisions: the named recipe constructs
    them, then its recorded actions, forced queues and fixture states must match.
    """
    record = json.loads(json.dumps(record))
    config = record['config']
    if config['origin'] == 'synthetic':
        from tests.interaction_scenarios import injury, movement, negatrait, push
        scenarios = dict(injury=injury, movement=movement, negatrait=negatrait, push=push)
        options = {key: config[key] for key in ('side', 'seed', 'variant')}
        if config['case'] == 'movement':
            options['pathfinding'] = config['pathfinding']
        probe = scenarios[config['case']](**options)
        assert probe.reproduction() == record, 'synthetic recipe/journal mismatch'
        return probe
    if config['origin'] != 'natural' or record['forced'] or record['fixtures']:
        raise ValueError('natural replay cannot apply synthetic placements or forced dice')
    keys = ('size', 'seed', 'pathfinding', 'turns', 'home', 'away', 'ogre', 'case', 'origin')
    probe = fresh(**{key: config[key] for key in keys})
    for action in record['actions']:
        probe.step(decode_action(probe.game, action))
    return probe


def validate_seconds(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('seconds must be finite and positive')


@contextmanager
def deadline(seconds):
    """Interrupt even a stuck action/pathfinder on the POSIX evidence runner."""
    validate_seconds(seconds)
    if not hasattr(signal, 'setitimer'):
        # Portable decision/engine limits still apply; CI's hard limit is POSIX.
        yield
        return
    def expired(signum, frame):
        raise TimeoutError('issue25 hard wall-time limit')
    previous = signal.signal(signal.SIGALRM, expired)
    timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, timer[0] - (time.monotonic() - started)), timer[1])
