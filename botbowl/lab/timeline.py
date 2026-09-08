"""Opt-in logical time for external control; see docs/lab/timeline.md.

Timeline owns the engine binding. Its exported records and checkpoint payload
are detached data. No wall clock, policy, RNG or trajectory step is a game tick.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Optional, Tuple

from .actions import ActionControl, ActionV1
from .observations import ObservationControl


@dataclass(frozen=True)
class TimelineContext:
    episode_id: str
    branch_id: str
    event_seq: int = 0
    decision_seq: int = 0
    activation_seq: Optional[int] = None
    team_turn_seq: Optional[int] = None
    drive_seq: Optional[int] = None
    half: Optional[int] = None
    round: Optional[int] = None

    def to_json(self):
        return asdict(self)


@dataclass(frozen=True)
class TimelineEvent:
    context: TimelineContext
    decision_seq: Optional[int]
    kind: str
    data: Dict[str, Any]

    def to_json(self):
        return asdict(self)


@dataclass(frozen=True)
class DecisionEnvelope:
    before: TimelineContext
    after: TimelineContext
    actor_id: str
    team_id: str
    action: ActionV1
    event_start: int
    event_stop: int
    events: Tuple[TimelineEvent, ...]
    next_actor_id: Optional[str]
    terminal: bool
    status: str
    macro_id: Optional[str] = None
    primitive_order: Optional[int] = None

    def to_json(self):
        result = asdict(self)
        result['action'] = self.action.to_json()
        result['events'] = [event.to_json() for event in self.events]
        return result


@dataclass(frozen=True)
class TimelineCheckpoint:
    """Trusted data payload for a checkpoint owner, not a SIM-02 codec."""
    context: TimelineContext
    events: tuple
    decisions: tuple
    macros: tuple
    operational_errors: tuple
    pending: Optional[int]
    activation_open: bool
    turn_open: bool
    drive_open: bool


class Timeline:
    """Attach once, before the first action, to an externally controlled game.

    IDs are supplied by the controller; defaults use the existing game ID and
    the root branch. Construction and instrumentation consume no randomness.
    Read properties/to_json return copies, including nested action options.
    """

    def __init__(self, game, *, episode_id=None, branch_id='root'):
        episode_id = game.game_id if episode_id is None else episode_id
        for value in (episode_id, branch_id):
            if type(value) is not str or not value:
                raise ValueError('Timeline IDs must be nonempty strings')
        if not game.external_control:
            raise ValueError('Timeline requires external_control=True')
        if game.timeline is not None or game.state.reports or game.start_time is not None:
            raise ValueError('Attach one timeline before the first game action')
        self._game = game
        self._entities = ObservationControl(game)
        self._context = TimelineContext(episode_id, branch_id)
        self._events = []
        self._decisions = []
        self._macros = []
        self._operational_errors = []
        self._pending = None
        self._parent = (None, None)
        self._activation_open = self._turn_open = self._drive_open = False
        game.timeline = self

    @property
    def context(self):
        return self._context

    @property
    def events(self):
        return deepcopy(tuple(self._events))

    @property
    def decisions(self):
        return deepcopy(tuple(self._decisions))

    def decisions_since(self, count):
        return deepcopy(tuple(self._decisions[count:]))

    def to_json(self):
        return {'schema_version': 1, 'context': self.context.to_json(),
                'events': [event.to_json() for event in self._events],
                'decisions': [decision.to_json() for decision in self._decisions],
                'macros': deepcopy(self._macros),
                'operational_errors': deepcopy(self._operational_errors)}

    def _prepare(self, action):
        if action is None:
            return None
        return ActionControl(self._game, self._entities).encode(action)

    def _begin(self, action):
        if action is None:
            return
        if self._pending is not None:
            raise RuntimeError('A timeline decision is still resolving')
        before = self.context
        self._context = replace(before, decision_seq=before.decision_seq + 1)
        self._pending = len(self._decisions)
        self._decisions.append(DecisionEnvelope(
            before, self.context, action.actor_id, action.actor_id, deepcopy(action),
            before.event_seq + 1, before.event_seq + 1, (), None, False, 'pending',
            *self._parent))

    def _settle(self, failed=False):
        """Retain a partial action on operational failure, then finish on resume."""
        if self._pending is None:
            return ()
        game = self._game
        record = self._decisions[self._pending]
        settled = (bool(game.state.available_actions) and not failed) or game.state.game_over
        record = replace(record, after=self.context,
                         event_stop=self.context.event_seq + 1,
                         events=tuple(self._events[record.event_start - 1:]),
                         next_actor_id=None if game.state.game_over else self._entities._team(game.active_team),
                         terminal=game.state.game_over,
                         status='resolved' if settled else 'pending')
        self._decisions[self._pending] = record
        if settled:
            self._pending = None
        return (deepcopy(record),)

    def _failure(self, error):
        # An exception object, traceback or callback never enters trace data.
        self._operational_errors.append({
            'context': self.context.to_json(), 'error_type': type(error).__name__,
            'code': str(getattr(error, 'code', 'engine_error'))})

    def _emit(self, kind, data):
        self._context = replace(self.context, event_seq=self.context.event_seq + 1)
        cause = None if self._pending is None else self.context.decision_seq
        self._events.append(TimelineEvent(self.context, cause, kind, deepcopy(data)))

    def _phase(self, kind, **data):
        if kind == 'half_started':
            self._context = replace(self.context, half=self._game.state.half,
                                    round=self._game.state.round)
        elif kind == 'round_started':
            self._context = replace(self.context, round=self._game.state.round)
        elif kind in ('drive_started', 'team_turn_started', 'activation_started'):
            field = {'drive_started': 'drive_seq', 'team_turn_started': 'team_turn_seq',
                     'activation_started': 'activation_seq'}[kind]
            self._context = replace(self.context, **{field: (getattr(self.context, field) or 0) + 1})
            setattr(self, {'drive_started': '_drive_open', 'team_turn_started': '_turn_open',
                           'activation_started': '_activation_open'}[kind], True)
        elif kind in ('drive_ended', 'team_turn_ended', 'activation_ended'):
            field = {'drive_ended': '_drive_open', 'team_turn_ended': '_turn_open',
                     'activation_ended': '_activation_open'}[kind]
            if not getattr(self, field):
                return
            setattr(self, field, False)
        # Engine references are resolved here, never retained in event payloads.
        if 'team' in data:
            data['team_id'] = self._entities._team(data.pop('team'))
        if 'player' in data:
            data['player_id'] = self._entities._player(data.pop('player'))
        self._emit(kind, data)

    def _report(self, outcome):
        data = outcome.to_json()
        data.update(player_id=self._entities._player(outcome.player),
                    opp_player_id=self._entities._player(outcome.opp_player),
                    team_id=self._entities._team(outcome.team))
        self._emit('report', data)
        if outcome.outcome_type.name in ('END_OF_FIRST_HALF', 'END_OF_SECOND_HALF'):
            self._phase('drive_ended', reason='half_end')

    @contextmanager
    def primitive(self, macro_id, order):
        previous = self._parent
        self._parent = (macro_id, order)
        try:
            yield
        finally:
            self._parent = previous

    def _macro_result(self, macro, before, result):
        self._macros.append({
            'macro': deepcopy(macro.to_json()), 'before': before.to_json(),
            'after': self.context.to_json(), 'status': result.status,
            'decision_seqs': [decision.after.decision_seq
                              for decision in self._decisions[before.decision_seq:]
                              if decision.macro_id == macro.macro_id],
            'interruption': None if result.interruption is None else {
                'reason': result.interruption, 'next_order': len(result.steps),
                'actor_id': self._entities._team(self._game.active_team),
                'context': self.context.to_json()}})

    def capture(self):
        if self._parent != (None, None):
            raise ValueError('Capture between macro calls, not inside an expansion')
        return deepcopy(TimelineCheckpoint(
            self.context, tuple(self._events), tuple(self._decisions), tuple(self._macros),
            tuple(self._operational_errors), self._pending, self._activation_open,
            self._turn_open, self._drive_open))

    def _validate_checkpoint(self, checkpoint):
        if (type(checkpoint) is not TimelineCheckpoint or
                checkpoint.context.episode_id != self.context.episode_id):
            raise ValueError('Expected a trusted checkpoint for this timeline episode')

    def restore(self, checkpoint):
        """Restore with matching engine state; owner must validate engine first."""
        self._validate_checkpoint(checkpoint)
        checkpoint = deepcopy(checkpoint)
        self._context = checkpoint.context
        self._events = list(checkpoint.events)
        self._decisions = list(checkpoint.decisions)
        self._macros = list(checkpoint.macros)
        self._operational_errors = list(checkpoint.operational_errors)
        self._pending = checkpoint.pending
        self._activation_open = checkpoint.activation_open
        self._turn_open = checkpoint.turn_open
        self._drive_open = checkpoint.drive_open

    def fork(self, branch_id):
        """Name a restored continuation; existing prefix keeps its branch IDs."""
        if (type(branch_id) is not str or not branch_id or
                branch_id == self.context.branch_id or
                any(event.context.branch_id == branch_id for event in self._events)):
            raise ValueError('Supply a new nonempty branch ID')
        if self._pending is not None or self._parent != (None, None):
            raise ValueError('Fork only at a completed decision boundary')
        self._context = replace(self.context, branch_id=branch_id)
