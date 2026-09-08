"""Trusted, process-local executable snapshots. See docs/lab/snapshots.md.

No pickle, viewer JSON, arbitrary deepcopy hooks, or live component objects enter
this payload. Each graph copy has one memo, including all procedure contexts.
"""
from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import math
import os
import time

import numpy as np

from botbowl.core import model, procedure
from botbowl.core.forward_model import (Reversible, ReversibleDict, ReversibleList,
                                       ReversibleSet, Trajectory)
from botbowl.core.game import Game
from botbowl.core.util import Stack
from .actions import ActionV1, PositionV1, EmptyOptionsV1, SkillOptionsV1, PathOptionsV1
from .episodes import EpisodeContext
from .observations import ObservationControl
from .randomness import SeedSpec, capture_stream
from .rules import _ARENA_TILE_GROUPS, _backend_id, _digest, _rules
from .timeline import (DecisionEnvelope, Timeline, TimelineCheckpoint,
                       TimelineContext, TimelineEvent)


VERSION = 1
# Explicit roots: everything else on Game is rejected, not silently dropped.
_GAME_DATA = ('game_id', 'config', 'arena', 'ruleset', 'state', 'action',
              'external_control', '_initialized', '_closed', '_end_notified',
              'start_time', 'end_time', 'last_request_time', 'last_action_time')
_GAME_SPECIAL = ('home_agent', 'away_agent', 'dice', 'trajectory', 'timeline', 'rule_trace',
                 'time_source', 'square_shortcut', 'ff_map', 'replay',
                 'finalization_errors', '_snapshot_busy', '_snapshot_ready')
_EPISODE_DATA = ('_seed', '_ids', '_inputs', '_max_decisions', '_max_steps',
                 '_initial_teams', '_manifest', 'decisions', '_streams', '_control')
_EPISODE_SPECIAL = ('game', '_policies', '_scenario', '_snapshot_busy')
_MODEL_TYPES = tuple(getattr(model, name) for name in (
    'Configuration', 'TimeLimits', 'GameState', 'Pitch', 'Team', 'TeamState',
    'Player', 'PlayerState', 'Role', 'Race', 'RuleSet', 'Inducement', 'Formation',
    'Dugout', 'Square', 'Ball', 'Bomb', 'Action', 'ActionChoice', 'Outcome',
    'DiceRoll', 'D3', 'D6', 'D8', 'BBDie', 'TwoPlayerArena'))
_PROCEDURE_TYPES = tuple(value for value in vars(procedure).values()
                         if isinstance(value, type) and value.__module__ == procedure.__name__
                         and issubclass(value, procedure.Procedure))
_DATA_TYPES = (SeedSpec, ActionV1, PositionV1, EmptyOptionsV1, SkillOptionsV1,
               PathOptionsV1, TimelineContext, TimelineEvent,
               DecisionEnvelope, TimelineCheckpoint)

# Instance fields declared by the accepted engine; inherited fields are unioned.
# Context remains the explicit procedure graph extension slot. New fields need
# an inventory update rather than silently entering the capture contract.
_INSTANCE_FIELDS = {
    'TimeLimits': 'end init secondary turn',
    'Configuration': (
        'arena competition_mode debug_mode defensive_formations dungeon fast_mode kick_off_table '
        'kick_scatter_dice kick_scatter_distance name offensive_formations '
        'pathfinding_directly_to_adjacent pathfinding_enabled pitch_max pitch_min roster_size '
        'rounds ruleset scrimmage_min throw_in_dice time_limits wing_max'),
    'PlayerState': (
        'always_show_attr show_if_true_attr blood_lust bone_headed ejected failed_nega_trait_this_turn has_blocked heated '
        'hypnotized in_air injuries_gained knocked_out moves picked_up really_stupid '
        'spp_earned squares_moved stunned taken_root up used used_skills wild_animal'),
    'TeamState': (
        'apothecaries ass_coaches babes bribes cheerleaders fame masterchef reroll_used '
        'rerolls rerolls_start score time_violation turn wizard_available'),
    'GameState': (
        'active_player available_actions away_team clocks coin_toss_winner current_team '
        'dugouts game_over gentle_gust half home_team kicking_first_half kicking_this_drive '
        'pitch player_action_type player_by_id receiving_first_half receiving_this_drive '
        'reports rerolled_procs round spectators stack team_by_id team_by_player_id teams '
        'turn_order weather'),
    'Pitch': 'balls board bomb height squares width',
    'ActionChoice': 'action_type block_dice disabled paths players positions rolls skill team',
    'Action': 'action_type player position',
    'TwoPlayerArena': (
        'away_td_tiles away_tiles board height home_td_tiles home_tiles json scrimmage_tiles '
        'width wing_left_tiles wing_right_tiles'),
    'DiceRoll': (
        'd68 dice highest_succeed lowest_fail modifiers roll_type sum target target_higher '
        'target_lower'),
    'D3': 'value',
    'D6': 'value',
    'D8': 'value',
    'BBDie': 'value',
    'Dugout': 'casualties dungeon kod reserves team',
    'Role': 'ag av cost d_skill_sets feeder ma n_skill_sets name races skills st star_player',
    'Piece': 'position',
    'Catchable': 'is_carried on_ground position',
    'Ball': '',
    'Bomb': '',
    'Player': (
        'extra_ag extra_av extra_ma extra_skills extra_st injuries mng name nr player_id role '
        'spp state team'),
    'Square': '_out_of_bounds x y',
    'Race': 'apothecary name reroll_cost roles stakes',
    'Team': (
        'apothecaries ass_coaches cheerleaders fan_factor name players race rerolls state '
        'team_id treasury'),
    'Outcome': 'n opp_player outcome_type player position rolls skill team',
    'Inducement': 'cost max_num name reduced',
    'RuleSet': (
        'improvements inducements name races se_interval se_pace se_start spp_actions '
        'spp_levels star_players'),
    'Formation': 'formation name',
    'Procedure': 'context done game started',
    'Regeneration': 'player regenerates',
    'Apothecary': (
        'casualty casualty_first casualty_second decay decay_roll effect effect_first '
        'effect_second inflictor outcome player regeneration roll roll_first roll_second '
        'waiting_apothecary'),
    'Armor': 'armor_rolled ejected foul inflictor modifiers player skip_armor',
    'Stab': 'attacker blitz defender foul_appearance gfi reroll roll',
    'FoulAppearance': 'attacker defender reroll revolted roll',
    'Block': (
        'attacker blitz dauntless_roll dauntless_success defender favor foul_appearance '
        'frenzy_block frenzy_checked gfi juggernaut_checked reroll roll selected_die '
        'waiting_dump_off waiting_foul_appearance waiting_juggernaut waiting_wrestle_attacker '
        'waiting_wrestle_defender'),
    'Bounce': 'kick piece',
    'Casualty': (
        'always_hungry blood_lust casualty decay decay_roll effect inflictor player '
        'regeneration roll waiting_apothecary'),
    'Catch': 'accurate bomb_choice diving handoff kick passer piece player reroll roll',
    'Intercept': (
        'ball interceptor passer reroll roll safe_throw_reroll safe_throw_roll '
        'waiting_safe_throw'),
    'CoinTossFlip': '',
    'CoinTossKickReceive': 'aa',
    'Ejection': 'awaiting_bribe player',
    'Foul': 'defender fouler',
    'ResetHalf': '',
    'Half': 'half kicked_off prepared',
    'Injury': (
        'blood_lust dirty_player_used ejected foul in_crowd inflictor injury_rolled '
        'mighty_blow_used player stab'),
    'Interception': 'ball interceptors passer team',
    'Touchback': 'ball players_on_pitch_standing',
    'LandKick': 'ball landed',
    'Fans': '',
    'Kickoff': '',
    'GetTheRef': '',
    'Riot': 'effect',
    'HighKick': 'available_players ball receiving_team',
    'CheeringFans': '',
    'BrilliantCoaching': '',
    'ThrowARock': 'rolled',
    'PitchInvasionRoll': 'player team',
    'KickoffTable': 'ball rolled',
    'KnockDown': (
        'armor_roll blood_lust in_crowd inflictor injury_roll modifiers modifiers_opp player '
        'stab turnover'),
    'KnockOut': 'inflictor player roll',
    'Leap': 'player position reroll roll',
    'Shadowing': 'player position reroll roll shadower shadowers team',
    'Tentacles': 'move_proc player position reroll roll tentacler',
    'Move': 'dodge dodge_proc gfi player position tentaclers tentacles_used',
    'GFI': 'player position reroll roll',
    'Dodge': (
        'break_tackle_target diving_tackler diving_tacklers from_position player position '
        'reroll roll waiting_break_tackle'),
    'TurnoverIfPossessionLost': 'ball',
    'Handoff': 'ball catcher eat_thrall player pos_to',
    'Explode': 'bomb player',
    'Land': 'player reroll roll',
    'PassAttempt': (
        'catcher dump_off eat_thrall fumble interception_tried pass_distance passer piece '
        'position reroll roll safe_throw_used ttm turnover'),
    'Pickup': 'ball player reroll roll',
    'StandUp': 'moves_required player reroll roll roll_required sroll_required',
    'PlaceBall': 'aa ball',
    'EndPlayerTurn': 'player',
    'JumpUpToBlock': 'player reroll roll',
    'EscapeBeingEaten': 'delicious_player hungry_player reroll roll',
    'AlwaysHungry': 'delicious_player hungry_player reroll roll',
    'UndoPlayerAction': 'game player',
    'MoveAction': 'can_undo orig_action_type paths player player_action_type steps',
    'HandoffAction': 'can_undo steps',
    'PassAction': 'can_undo dump_off picked_up_teammate',
    'ThrowBombAction': 'can_undo player',
    'FoulAction': 'can_undo steps',
    'BlockAction': 'can_undo player',
    'Frenzy': 'attacker blitz defender first_block',
    'BlitzAction': 'can_undo player steps',
    'StartGame': '',
    'EndGame': '',
    'Pregame': '',
    'PreKickoff': 'checked team',
    'FollowUp': 'attacker defender pos_to',
    'Push': (
        'blitz chain crowd follow_to knock_down player player_chain push_to pusher selector '
        'squares stand_firm_used strip_ball_condition waiting_for_move waiting_stand_firm'),
    'Scatter': 'gentle_gust is_pass kick piece',
    'ClearBoard': '',
    'Setup': 'aa formations reorganize selected_player team',
    'ThrowIn': 'ball position',
    'Turnover': '',
    'Touchdown': 'eat_thrall handle_bloodlust player',
    'TurnStunned': 'team',
    'EndTurn': 'kickoff',
    'Turn': (
        'blitz blitz_available foul_available half handoff_available pass_available '
        'quick_snap team turn'),
    'WeatherTable': 'kickoff',
    'Negatrait': (
        'ends_turn fail_outcome player reroll reroll_used roll roll_type rolled skill '
        'success_outcome waiting_reroll'),
    'Bonehead': 'fail_outcome roll_type skill success_outcome',
    'ReallyStupid': 'fail_outcome roll_type skill success_outcome',
    'WildAnimal': 'fail_outcome is_block_or_blitz roll_type skill success_outcome',
    'TakeRoot': 'fail_outcome roll_type skill success_outcome',
    'BloodLustBlockOrMove': 'player',
    'BloodLust': 'fail_outcome is_block roll_type skill success_outcome',
    'Reroll': (
        'block_action block_actions can_use_pro can_use_team_reroll loner player pro '
        'secondary_clock skill use_reroll'),
    'Pro': 'player reroll roll success',
    'Loner': 'player reroll result roll success',
    'EatThrall': 'failed player victim victim_pos',
    'HypnoticGaze': 'player reroll roll target_player',
    'Stack': 'items',
    'ObservationControl': '_game _player_ids _rosters _team_ids _teams',
    'Timeline': (
        '_activation_open _context _decisions _drive_open _entities _events _game _macros '
        '_operational_errors _parent _pending _turn_open'),
    'LogicalTime': 'value',
}


class SnapshotError(ValueError):
    code = 'snapshot_invalid'


@dataclass
class LogicalTime:
    """Explicit lab seconds; reading or executing a decision does not tick time."""
    value: float = 0.0

    def __post_init__(self):
        if type(self.value) not in (int, float) or not math.isfinite(self.value) or self.value < 0:
            raise SnapshotError('Logical time requires finite nonnegative seconds')

    def __call__(self):
        return self.value

    def advance(self, seconds):
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise SnapshotError('Logical time requires finite nonnegative seconds')
        self.value += seconds


class _ExternalAgent(model.Agent):
    """Seat metadata only; no policy or external lifecycle is retained."""
    def act(self, game):
        raise SnapshotError('Engine scope requires externally supplied actions')

    def new_game(self, game, team):
        pass

    def end_game(self, game):
        pass


@dataclass(frozen=True)
class ComponentState:
    adapter: str
    state: object


class SnapshotAdapters:
    """Local trusted codecs: capture(component, registry), restore(data, registry).

    Restore must construct a fresh component, with no I/O or live mutation.
    A wrapper codec must use capture/restore for every nested active component.
    Only detached data is accepted; registry functions never enter a snapshot.
    """
    def __init__(self):
        self._by_type = {}
        self._by_name = {}
        self._capturing = self._restoring = None

    def register(self, name, component_type, capture, restore):
        if (type(name) is not str or not name or name in self._by_name or
                component_type in self._by_type or not isinstance(component_type, type) or
                not callable(capture) or not callable(restore)):
            raise SnapshotError('Register a unique adapter name and exact component type')
        self._by_type[component_type] = name
        self._by_name[name] = (component_type, capture, restore)
        return self

    def _capture_components(self, components, graph=None):
        if self._capturing is not None:
            raise SnapshotError('Snapshot adapter capture is already active')
        self._capturing = {}
        try:
            return _copy_data({name: self.capture(value) for name, value in components.items()}, graph)
        finally:
            self._capturing = None

    def capture(self, component):
        if self._capturing is None:
            return self._capture_components({'component': component})['component']
        if id(component) in self._capturing:
            result = self._capturing[id(component)]
            if result is None:
                raise SnapshotError('Encode cyclic components as explicit data')
            return result
        try:
            name = self._by_type[type(component)]
        except KeyError as error:
            raise SnapshotError('Unadapted component: ' + type(component).__qualname__) from error
        self._capturing[id(component)] = None
        try:
            result = ComponentState(name, self._by_name[name][1](component, self))
            self._capturing[id(component)] = result
            return result
        except Exception as error:
            raise SnapshotError('Component capture failed: ' + name) from error

    def _restore_components(self, components, graph=None):
        if self._restoring is not None:
            raise SnapshotError('Snapshot adapter restore is already active')
        self._restoring = {}
        try:
            detached = _copy_data(components, graph)
            return {name: self.restore(value) for name, value in detached.items()}
        finally:
            self._restoring = None

    def restore(self, payload):
        if self._restoring is None:
            return self._restore_components({'component': payload})['component']
        if type(payload) is not ComponentState or payload.adapter not in self._by_name:
            raise SnapshotError('Missing registered snapshot adapter')
        if id(payload) in self._restoring:
            result = self._restoring[id(payload)]
            if result is None:
                raise SnapshotError('Decode cyclic components from explicit data')
            return result
        self._restoring[id(payload)] = None
        kind, _, restore = self._by_name[payload.adapter]
        try:
            result = restore(payload.state, self)
            if type(result) is not kind:
                raise TypeError('Adapter returned the wrong component type')
            self._restoring[id(payload)] = result
            return result
        except Exception as error:
            raise SnapshotError('Component restore failed: ' + payload.adapter) from error


@dataclass(frozen=True)
class Snapshot:
    version: int
    scope: str
    undo_origin: int
    _pid: int
    _game: Game
    _episode: object
    _components: object
    _source_scopes: tuple
    _compatibility: object
    _seal: str

    def __reduce_ex__(self, protocol):
        raise TypeError('Snapshots are process-local; persistence belongs to SIM-03')


def _immutable_enum_value(value):
    return type(value) in (int, str) or (type(value) is tuple and
                                       all(_immutable_enum_value(item) for item in value))


def _check_fields(value):
    kind = type(value)
    if kind in (*_DATA_TYPES, ComponentState):
        allowed = {field.name for field in fields(kind)}
    else:
        allowed = {key for base in kind.__mro__
                   for key in _INSTANCE_FIELDS.get(base.__name__, '').split()}
        if isinstance(value, Reversible):
            allowed.update(('_trajectory', '_ignored_keys'))
        if kind in (model.ActionChoice, model.Outcome, model.Square):
            allowed.add('__setattr__')
    if set(vars(value)) - allowed:
        raise SnapshotError('Uninventoried fields on ' + kind.__qualname__)


def _copy_data(value, graph=None):
    graph = _Graph() if graph is None else graph
    previous = graph.data_only
    graph.data_only = True
    try:
        return graph.copy(value)
    finally:
        graph.data_only = previous


class _Graph:
    """Copy only named engine classes and data, never __reduce__/__deepcopy__."""
    def __init__(self, *, data_only=False):
        self.memo = {}
        self.data_only = data_only
        self.trajectory = Trajectory()
        self.time = LogicalTime()
        self.objects = []
        self._tuples = set()

    def copy(self, value):
        kind = type(value)
        if kind in (type(None), bool, int, float, str, bytes):
            return value
        if isinstance(value, Enum) and kind.__module__ == 'botbowl.core.table':
            if (set(vars(value)) != {'_value_', '_name_', '__objclass__', '_sort_order_'} or
                    not _immutable_enum_value(value.value)):
                raise SnapshotError('Engine enum has mutable extension state')
            return value
        if isinstance(value, np.generic) and value.dtype.kind in 'biuf':
            return value.item()
        if self.data_only and kind not in (list, tuple, dict, set, frozenset, ComponentState,
                                            np.ndarray, np.random.RandomState):
            raise SnapshotError('Unsupported object in component data: ' + kind.__qualname__)
        if id(value) in self.memo:
            return self.memo[id(value)]
        if kind is np.random.RandomState:
            result = np.random.RandomState(0)
            result.set_state(capture_stream(value))
            self.memo[id(value)] = result
            return result
        if kind is np.ndarray and value.dtype.kind in 'biufUSO':
            result = value.copy()
            self.memo[id(value)] = result
            if value.dtype.kind == 'O':
                for index in np.ndindex(value.shape):
                    result[index] = self.copy(value[index])
            return result
        if kind in (list, dict, set, tuple, frozenset, ReversibleList, ReversibleDict, ReversibleSet):
            if self.data_only and kind in (ReversibleList, ReversibleDict, ReversibleSet):
                raise SnapshotError('Component state must be detached data')
            if kind in (tuple, frozenset):
                # Engine tuples are not cyclic; list/dict/object cycles use the memo.
                if id(value) in self._tuples:
                    raise SnapshotError('Cyclic tuples are outside the engine inventory')
                self._tuples.add(id(value))
                try:
                    result = kind(self.copy(item) for item in value)
                finally:
                    self._tuples.remove(id(value))
            else:
                result = kind() if kind in (list, dict, set) else kind(())
                self.memo[id(value)] = result
                if isinstance(value, dict):
                    for key, item in value.items():
                        dict.__setitem__(result, self.copy(key), self.copy(item))
                elif isinstance(value, list):
                    for item in value:
                        list.append(result, self.copy(item))
                else:
                    for item in value:
                        set.add(result, self.copy(item))
                if isinstance(value, Reversible):
                    object.__setattr__(result, '_trajectory', self.trajectory if value._trajectory is not None else None)
                    self.objects.append(result)
            self.memo[id(value)] = result
            return result
        allowed = (ComponentState,) if self.data_only else (
            *_MODEL_TYPES, *_PROCEDURE_TYPES, *_DATA_TYPES, ComponentState, Stack,
            ObservationControl, LogicalTime)
        if kind not in allowed:
            raise SnapshotError('Unsupported object in snapshot graph: ' + kind.__qualname__)
        _check_fields(value)
        result = object.__new__(kind)
        self.memo[id(value)] = result
        self.objects.append(result)
        for key, item in vars(value).items():
            if key == '_trajectory' and isinstance(value, Reversible):
                copied = self.trajectory if item is not None else None
            elif key == 'json' and kind is model.TwoPlayerArena:
                copied = None
            elif key == 'paths' and isinstance(value, (procedure.MoveAction, model.ActionChoice)):
                copied = {} if isinstance(value, procedure.MoveAction) else []
                self.memo[id(item)] = copied
            else:
                copied = self.copy(item)
            object.__setattr__(result, key, copied)
        class_fields = (_ARENA_TILE_GROUPS if kind is model.TwoPlayerArena else
                        ('always_show_attr', 'show_if_true_attr') if kind is model.PlayerState else ())
        for key in class_fields:
            object.__setattr__(result, key, self.copy(getattr(value, key)))
        return result

    def game(self, source):
        result = object.__new__(Game)
        if type(source.time_source) is LogicalTime:
            self.time.value = source.time_source.value
        self.memo[id(source)] = result
        self.memo[id(source.trajectory)] = self.trajectory
        result.dice = _dice(source.capture_rng_state())
        self.memo[id(source.dice)] = result.dice
        self.memo[id(source.rng)] = result.rng
        self.memo[id(source.time_source)] = self.time if source.time_source is not None else None
        clock_now = time.monotonic() if source.time_source is None else self.time.value
        # Clock callables never traverse the graph; preserve elapsed logical time.
        for clock in source.state.clocks:
            if type(clock) is not model.Clock or clock.time_source is not source.time_source:
                raise SnapshotError('Clock has an unsupported time source')
            cloned = object.__new__(model.Clock)
            self.memo[id(clock)] = cloned
            cloned.seconds = self.copy(clock.seconds)
            cloned.is_primary = self.copy(clock.is_primary)
            cloned.started_at = self.copy(clock.started_at)
            cloned.paused_seconds = self.copy(clock.paused_seconds)
            if type(source.time_source) is LogicalTime:
                cloned._started_at = clock._started_at
                cloned.paused_at = clock.paused_at
            else:
                elapsed = (clock_now if clock.is_running() else clock.paused_at) - clock._started_at - clock.paused_seconds
                cloned._started_at = self.time.value - elapsed - cloned.paused_seconds
                cloned.paused_at = None if clock.is_running() else self.time.value
            cloned.time_source = self.time
        for field in _GAME_DATA:
            setattr(result, field, self.copy(getattr(source, field)))
        for clock in source.state.clocks:
            self.memo[id(clock)].team = self.copy(clock.team)
        if hasattr(source, 'seed'):
            result.seed = self.copy(source.seed)
        for side in ('home', 'away'):
            agent = getattr(source, side + '_agent')
            if id(agent) not in self.memo:
                self.memo[id(agent)] = _ExternalAgent(
                    self.copy(agent.name), human=self.copy(agent.human), agent_id=self.copy(agent.agent_id))
            setattr(result, side + '_agent', self.memo[id(agent)])
        result.trajectory = self.trajectory
        result.square_shortcut = result.state.pitch.squares
        result.time_source = self.time
        result.ff_map = result.replay = None
        result.finalization_errors = []
        result._snapshot_busy, result._snapshot_ready = 0, True
        result.timeline = None
        result.rule_trace = None
        if source.timeline is not None:
            _check_fields(source.timeline)
            timeline = object.__new__(Timeline)
            self.memo[id(source.timeline)] = timeline
            for key, value in vars(source.timeline).items():
                setattr(timeline, key, self.copy(value))
            result.timeline = timeline
        return result


def _dice(state):
    """Transfer every forced frame as owned state, without a live context manager."""
    if (type(state) is not model.DiceSourceState or not state.queues or
            len(state.strict) != len(state.queues) or len(state._scopes) != len(state.queues) - 1):
        raise SnapshotError('Malformed dice frame inventory')
    result = model.DiceSource(0)
    try:
        result.rng.set_state(state.rng_state)
        queues = []
        for frame in state.queues:
            if len(frame) != 4:
                raise ValueError('Expected four dice queues')
            row = {}
            for die, values in zip((model.D3, model.D6, model.D8, model.BBDie), frame):
                model.DiceSource._validate(die, values)
                row[die] = list(values)
            queues.append(row)
        if any(type(strict) is not bool for strict in state.strict):
            raise ValueError('Expected boolean strict flags')
    except (TypeError, ValueError) as error:
        raise SnapshotError('Malformed RNG or forced queues') from error
    result._queues, result._strict = queues, list(state.strict)
    result._scopes = [object() for _ in state._scopes]
    return result


def _boundary(game):
    if (type(game) is not Game or game._snapshot_busy or not game._snapshot_ready or
            not game._initialized or (game.closed and not game.state.game_over)):
        raise SnapshotError('Capture requires a settled decision or terminal boundary')
    if type(game.dice) is not model.DiceSource or type(game.rng) is not np.random.RandomState:
        raise SnapshotError('Expected the owned engine MT19937 dice source')
    if set(vars(game.dice)) != {'rng', '_queues', '_strict', '_scopes'}:
        raise SnapshotError('Uninventoried dice source fields')
    if game.time_source is not None and type(game.time_source) is not LogicalTime:
        raise SnapshotError('Use the default clock or LogicalTime for lab snapshots')
    if game.finalization_errors:
        raise SnapshotError('A failed finalizer is not a stable terminal boundary')
    # A child decision can suspend an ancestor's selected route. Those remaining
    # steps are continuation state; only an active automatic route is ineligible.
    if game.state.game_over:
        if game.state.available_actions or not game._end_notified:
            raise SnapshotError('Terminal finalization has not settled')
    elif (not game.state.available_actions or game.state.stack.is_empty() or
          game.get_procedure().done or
          (isinstance(game.get_procedure(), procedure.MoveAction) and game.get_procedure().steps)):
        raise SnapshotError('Automatic procedure continuation is not a decision boundary')
    if game.timeline is not None and (type(game.timeline) is not Timeline or
            game.timeline._pending is not None or game.timeline._parent != (None, None)):
        raise SnapshotError('Timeline decision or macro is still resolving')
    if game.rule_trace is not None:
        raise SnapshotError('Live rule traces are not snapshot continuation data')
    unknown = set(vars(game)) - set(_GAME_DATA + _GAME_SPECIAL + ('seed',))
    if unknown:
        raise SnapshotError('Uninventoried Game fields: ' + ', '.join(sorted(unknown)))


def _structure(game, objects):
    state = game.state
    if type(state) is not model.GameState or type(state.pitch) is not model.Pitch:
        raise SnapshotError('Invalid game state or pitch')
    teams = (state.home_team, state.away_team)
    if (len(state.teams) != 2 or any(a is not b for a, b in zip(state.teams, teams)) or
            teams[0].team_id == teams[1].team_id):
        raise SnapshotError('Invalid team identities')
    players = [player for team in teams for player in team.players]
    if len({p.player_id for p in players}) != len(players):
        raise SnapshotError('Duplicate player identities')
    by_id = {p.player_id: p for p in players}
    for team in teams:
        if any(player.team is not team for player in team.players):
            raise SnapshotError('Foreign player/team reference')
    pitch = state.pitch
    if ((pitch.width, pitch.height) != (game.arena.width, game.arena.height) or
            len(pitch.board) != pitch.height or len(pitch.squares) != pitch.height or
            any(len(row) != pitch.width for row in (*pitch.board, *pitch.squares))):
        raise SnapshotError('Pitch dimensions disagree')
    for y, row in enumerate(pitch.board):
        for x, player in enumerate(row):
            if player is not None and (by_id.get(player.player_id) is not player or
                    player.position is None or (player.position.x, player.position.y) != (x, y)):
                raise SnapshotError('Board/player reference mismatch')
            square = pitch.squares[y][x]
            if type(square) is not model.Square or (square.x, square.y) != (x, y):
                raise SnapshotError('Invalid square index')
    for player in players:
        pos = player.position
        if pos is not None and (not 0 <= pos.x < pitch.width or not 0 <= pos.y < pitch.height or
                               pitch.board[pos.y][pos.x] is not player):
            raise SnapshotError('Player is missing from the board')
    for obj in objects:
        if isinstance(obj, procedure.Procedure) and obj.game is not game:
            raise SnapshotError('Procedure references a foreign Game')
        if type(obj) is ObservationControl:
            obj._check(game)
        if type(obj) is model.Player and obj.player_id in by_id and by_id[obj.player_id] is not obj:
            raise SnapshotError('Noncanonical player reference')
    canonical_teams = {id(team) for team in teams}
    for dugout in state.dugouts.values():
        if id(dugout.team) not in canonical_teams:
            raise SnapshotError('Foreign dugout team')
        for group in (dugout.reserves, dugout.kod, dugout.casualties, dugout.dungeon):
            if any(by_id.get(player.player_id) is not player or player.team is not dugout.team
                   for player in group):
                raise SnapshotError('Foreign dugout player')
    for choice in state.available_actions:
        if id(choice.team) not in canonical_teams or any(
                by_id.get(player.player_id) is not player for player in choice.players):
            raise SnapshotError('Foreign decision participant')
    if state.active_player is not None and by_id.get(state.active_player.player_id) is not state.active_player:
        raise SnapshotError('Foreign active player')
    if any(id(clock.team) not in canonical_teams for clock in state.clocks):
        raise SnapshotError('Foreign clock owner')
    if any(type(getattr(state, key)) is not int or getattr(state, key) < 0 for key in ('half', 'round')):
        raise SnapshotError('Invalid engine time')
    if game.timeline is not None:
        timeline = game.timeline
        context = timeline.context
        if (timeline._game is not game or type(context) is not TimelineContext or
                type(context.event_seq) is not int or type(context.decision_seq) is not int or
                context.event_seq != len(timeline._events) or context.decision_seq != len(timeline._decisions)):
            raise SnapshotError('Invalid timeline counters or owner')
        if any(event.context.event_seq != index for index, event in enumerate(timeline._events, 1)):
            raise SnapshotError('Invalid timeline event sequence')
        for index, decision in enumerate(timeline._decisions, 1):
            if (decision.status != 'resolved' or decision.after.decision_seq != index or
                    decision.before.decision_seq != index - 1 or
                    not 1 <= decision.event_start <= decision.event_stop <= context.event_seq + 1 or
                    decision.events != tuple(timeline._events[decision.event_start - 1:decision.event_stop - 1])):
                raise SnapshotError('Invalid timeline decision envelope')
    # Rebuild in place so any aliases to the indexes remain owned by this graph.
    for mapping, pairs in (
        (state.team_by_id, ((t.team_id, t) for t in teams)),
        (state.player_by_id, by_id.items()),
        (state.team_by_player_id, ((p.player_id, p.team) for p in players)),
    ):
        dict.clear(mapping)
        dict.update(mapping, pairs)


def _identity(game):
    # Complete loaded resources, not live player resources; Rules tables remain
    # code-owned and must still match rather than being globally overwritten.
    graph = _Graph(data_only=False)
    resources = graph.copy((game.config, game.ruleset, game.arena))
    return _fingerprint(resources), _digest(_rules(game.ruleset)), _backend_id()


def _fingerprint(root):
    """Integrity over the executable graph, including aliases; never viewer JSON."""
    memo = {}
    keepalive = []
    def visit(value):
        kind = type(value)
        if kind in (type(None), bool, int, float, str, bytes):
            return kind.__name__, repr(value)
        if isinstance(value, Enum):
            return kind.__module__, kind.__qualname__, value.name
        if isinstance(value, np.generic):
            return visit(value.item())
        if id(value) in memo:
            return 'ref', memo[id(value)]
        memo[id(value)] = len(memo)
        keepalive.append(value)
        name = kind.__module__ + '.' + kind.__qualname__
        if kind is np.random.RandomState:
            data = visit(capture_stream(value))
        elif kind is model.DiceSource:
            state = value.get_state()
            data = visit((state.rng_state, state.queues, state.strict))
        elif kind is np.ndarray:
            data = (str(value.dtype), value.shape,
                    [visit(item) for item in value.flat] if value.dtype.kind == 'O' else value.tobytes())
        elif isinstance(value, dict):
            data = [(visit(k), visit(v)) for k, v in value.items()]
        elif isinstance(value, (list, tuple, set, frozenset)):
            data = [visit(v) for v in value]
        else:
            data = [(k, visit(v)) for k, v in sorted(vars(value).items())]
        return name, data
    return hashlib.sha256(repr(visit(root)).encode('utf-8')).hexdigest()


def _capture_snapshot(subject, *, scope='engine', adapters=None):
    """Capture Game or EpisodeContext at a settled boundary without changing it."""
    if scope not in ('engine', 'episode'):
        raise SnapshotError('Expected engine or episode scope')
    episode = subject if type(subject) is EpisodeContext else None
    if scope == 'episode' and episode is None:
        raise SnapshotError('Episode scope requires an EpisodeContext')
    if episode is not None and episode._snapshot_busy:
        raise SnapshotError('Episode callback or decision is still executing')
    game = subject if episode is None else episode.game
    _boundary(game)
    components = {}
    active = []
    if episode is not None:
        unknown = set(vars(episode)) - set(_EPISODE_DATA + _EPISODE_SPECIAL)
        if unknown:
            raise SnapshotError('Uninventoried episode components: ' + ', '.join(sorted(unknown)))
        episode._check_runtime()
        if scope == 'episode':
            adapters = SnapshotAdapters() if adapters is None else adapters
            active = [('policy-' + side, value) for side, value in episode._policies.items()]
            if episode._scenario is not None:
                active.append(('scenario', episode._scenario))
            for side in ('home', 'away'):
                agent = getattr(game, side + '_agent')
                if type(agent) not in (model.Agent, _ExternalAgent) or not agent.human:
                    active.append(('agent-' + side, agent))
    graph = _Graph()
    copied_game = graph.game(game)
    game_objects = list(graph.objects)
    episode_data = None if episode is None else {
        field: graph.copy(getattr(episode, field)) for field in _EPISODE_DATA}
    if scope == 'episode':
        components = adapters._capture_components(dict(active), graph)
        # Validate that registered codecs can construct their state before publication.
        adapters._restore_components(components)
    _structure(copied_game, game_objects)
    copied_game.trajectory.enabled = game.trajectory.enabled
    compatibility = _identity(game)
    payload = (copied_game, episode_data, components, compatibility)
    return Snapshot(VERSION, scope, 0, os.getpid(), copied_game, episode_data, components,
                    tuple(game.dice._scopes), compatibility,
                    _fingerprint((scope, tuple(id(s) for s in game.dice._scopes), payload)))


def capture_snapshot(subject, *, scope='engine', adapters=None):
    """Capture Game or EpisodeContext at a settled boundary without changing it."""
    try:
        return _capture_snapshot(subject, scope=scope, adapters=adapters)
    except SnapshotError:
        raise
    except Exception as error:
        raise SnapshotError('Malformed snapshot source') from error


def _prepare(snapshot, adapters, branch_id):
    if (type(snapshot) is not Snapshot or type(snapshot.version) is not int or
            snapshot.version != VERSION or snapshot._pid != os.getpid() or
            type(snapshot.undo_origin) is not int or
            snapshot.scope not in ('engine', 'episode') or snapshot.undo_origin != 0):
        raise SnapshotError('Unsupported or foreign-process snapshot')
    payload = (snapshot._game, snapshot._episode, snapshot._components, snapshot._compatibility)
    if _fingerprint((snapshot.scope, tuple(id(s) for s in snapshot._source_scopes), payload)) != snapshot._seal:
        raise SnapshotError('Snapshot integrity mismatch')
    if _identity(snapshot._game) != snapshot._compatibility:
        raise SnapshotError('Snapshot configuration, rules or backend changed')
    _boundary(snapshot._game)
    graph = _Graph()
    game = graph.game(snapshot._game)
    game_objects = list(graph.objects)
    episode = None
    if snapshot._episode is not None:
        episode = object.__new__(EpisodeContext)
        episode.game = game
        episode._snapshot_busy = 0
        for key, value in snapshot._episode.items():
            setattr(episode, key, graph.copy(value))
        episode._policies, episode._scenario = {}, None
        if snapshot.scope == 'episode':
            adapters = SnapshotAdapters() if adapters is None else adapters
            for name, component in adapters._restore_components(snapshot._components, graph).items():
                if name == 'scenario':
                    episode._scenario = component
                elif name.startswith('agent-'):
                    setattr(game, name[6:] + '_agent', component)
                else:
                    episode._policies[name[7:]] = component
    _structure(game, game_objects)
    if episode is not None:
        episode._control._check(game)
    if branch_id is not None:
        if game.timeline is None:
            raise SnapshotError('Branch IDs require an attached Timeline')
        game.timeline.fork(branch_id)
    # Recreate executable route choices only on the private graph. Suspended
    # MoveAction paths are recomputed by the engine when they become active.
    if not game.state.game_over and isinstance(game.get_procedure(), procedure.MoveAction):
        game.set_available_actions()
    if snapshot._game.trajectory.enabled:
        game.enable_forward_model()
    return episode if episode is not None else game


def clone_from_snapshot(snapshot, *, adapters=None, branch_id=None):
    """Return an independent Game/context, with a new undo origin at step zero."""
    try:
        return _prepare(snapshot, adapters, branch_id)
    except SnapshotError:
        raise
    except Exception as error:
        raise SnapshotError('Malformed snapshot graph') from error


def _restore_snapshot(subject, snapshot, *, adapters=None):
    """Prepare privately, then replace the target atomically. No callbacks at commit.

    External bindings to replaced players/timelines/streams must be reacquired.
    Transport revision belongs to the live consumer and must increment there.
    """
    episode = subject if type(subject) is EpisodeContext else None
    game = subject if episode is None else episode.game
    if (type(game) is not Game or game._snapshot_busy or
            (episode is not None and episode._snapshot_busy)):
        raise SnapshotError('Restore requires an idle Game or EpisodeContext')
    prepared = clone_from_snapshot(snapshot, adapters=adapters)
    if (episode is None) != (type(prepared) is Game):
        raise SnapshotError('Snapshot subject kind differs from the target')
    replacement = prepared if episode is None else prepared.game
    if _identity(game) != snapshot._compatibility:
        raise SnapshotError('Target configuration, rules or backend is incompatible')
    if episode is not None and episode._manifest != prepared._manifest:
        raise SnapshotError('Target episode provenance is incompatible')
    preserve_dice = bool(game.dice._scopes)
    if preserve_dice and tuple(game.dice._scopes) != snapshot._source_scopes:
        raise SnapshotError('Target has different live forced-roll contexts')
    dice = game.dice
    dice_data = dict(replacement.dice.__dict__, _scopes=dice._scopes) if preserve_dice else None
    # Rebind *every* occurrence of the staging Game with one last memo. It only
    # rewires owned objects, so no adapter runs and no live state changes here.
    memo = {id(replacement): game}
    if preserve_dice:
        memo[id(replacement.dice)] = dice
    def rebind(value):
        if id(value) in memo:
            return memo[id(value)]
        if type(value) in (type(None), bool, int, float, str, bytes) or isinstance(value, Enum):
            return value
        memo[id(value)] = value
        if isinstance(value, dict):
            pairs = [(rebind(key), rebind(item)) for key, item in value.items()]
            dict.clear(value)
            dict.update(value, pairs)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                list.__setitem__(value, index, rebind(item))
        elif type(value) is np.ndarray and value.dtype.kind == 'O':
            for index in np.ndindex(value.shape):
                value[index] = rebind(value[index])
        elif isinstance(value, (tuple, frozenset)):
            result = type(value)(rebind(item) for item in value)
            memo[id(value)] = result
            return result
        elif isinstance(value, set):
            items = [rebind(item) for item in value]
            set.clear(value)
            set.update(value, items)
        elif type(value) in (*_MODEL_TYPES, *_PROCEDURE_TYPES, *_DATA_TYPES, Stack,
                             ObservationControl, Timeline, Trajectory, LogicalTime, _ExternalAgent):
            for key, item in vars(value).items():
                object.__setattr__(value, key, rebind(item))
        return value
    for key, value in replacement.__dict__.items():
        replacement.__dict__[key] = rebind(value)
    if episode is not None:
        rebind(prepared._control)
        prepared.game = game
    if preserve_dice:
        dice.__dict__ = dice_data
        replacement.dice = dice
    game.__dict__ = replacement.__dict__
    if episode is not None:
        episode.__dict__ = prepared.__dict__
    return subject


def restore_snapshot(subject, snapshot, *, adapters=None):
    """Atomically restore a compatible idle target; return that same target.

    Reacquire views/player references afterwards and increment the consumer's
    own transport revision. Logical counters and undo origin are restored.
    """
    try:
        return _restore_snapshot(subject, snapshot, adapters=adapters)
    except SnapshotError:
        raise
    except Exception as error:
        raise SnapshotError('Malformed snapshot target') from error
