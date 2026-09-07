"""Copied public observations; no engine advancement or tactical feature queries.

Create ObservationControl once on the episode's Game before its first action.
Keep it in control code, and send only ObservationV1.to_json() to a policy.
See docs/lab/observations.md for availability and the partial-context boundary.
"""
from dataclasses import asdict, dataclass, fields
from typing import Generic, List, Literal, Optional, TypeVar

from botbowl.core import procedure as proc
from botbowl.core.table import Tile


T = TypeVar("T")
Side = Literal["home", "away"]
Phase = Literal[
    "unstarted", "terminal", "other", "start_game", "coin_toss",
    "kick_receive", "setup", "place_ball", "high_kick", "touchback",
    "turn", "move", "block_action", "blitz", "pass_action", "handoff_action",
    "foul_action", "block", "push", "follow_up", "reroll", "gfi", "dodge",
    "pickup", "catch", "pass", "interception", "intercept", "apothecary",
]


@dataclass(frozen=True)
class Presence(Generic[T]):
    value: Optional[T]
    present: bool


def _present(value):
    return Presence(value, value is not None)


@dataclass(frozen=True)
class Position:
    x: int
    y: int


def _position(square):
    return _present(None if square is None else Position(square.x, square.y))


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    tiles: List[List[str]]
    playable: List[List[bool]]


@dataclass(frozen=True)
class Attributes:
    ma: int
    st: int
    ag: int
    av: int


@dataclass(frozen=True)
class PlayerStatus:
    up: bool
    in_air: bool
    used: bool
    stunned: bool
    bone_headed: bool
    hypnotized: bool
    really_stupid: bool
    heated: bool
    knocked_out: bool
    ejected: bool
    wild_animal: bool
    taken_root: bool
    blood_lust: bool
    picked_up: bool
    has_blocked: bool
    failed_nega_trait_this_turn: bool
    moves: int
    spp_earned: int


@dataclass(frozen=True)
class PlayerObservation:
    id: str
    team: Side
    slot: int
    position: Presence[Position]
    location: Literal["pitch", "reserves", "ko", "casualties", "dungeon", "unplaced"]
    role: str
    attributes: Attributes
    role_attributes: Attributes
    extra_attributes: Attributes
    skills: List[str]
    used_skills: List[str]
    injuries: List[str]
    injuries_gained: List[str]
    mng: bool
    status: PlayerStatus
    squares_moved: List[Position]


@dataclass(frozen=True)
class TeamResources:
    rerolls: int
    rerolls_start: int
    reroll_used: bool
    apothecaries: int
    bribes: int
    babes: int
    wizard_available: bool
    masterchef: bool
    ass_coaches: int
    cheerleaders: int
    fame: int


@dataclass(frozen=True)
class TeamObservation:
    id: Side
    race: str
    score: int
    turn: int
    resources: TeamResources


@dataclass(frozen=True)
class BallObservation:
    position: Presence[Position]
    carrier: Presence[str]
    on_ground: bool
    is_carried: bool


@dataclass(frozen=True)
class MatchObservation:
    half: int
    round: int
    drive: Presence[int]
    kicking_team: Presence[Side]
    receiving_team: Presence[Side]
    weather: str
    game_over: bool


@dataclass(frozen=True)
class PromptOption:
    action_type: str
    team: Presence[Side]
    skill: Presence[str]
    disabled: bool


@dataclass(frozen=True)
class TurnContext:
    blitz: bool
    quick_snap: bool
    blitz_available: bool
    pass_available: bool
    handoff_available: bool
    foul_available: bool


@dataclass(frozen=True)
class DecisionContext:
    phase: Phase
    pending: bool
    actor_team: Presence[Side]
    active_team: Presence[Side]
    active_player: Presence[str]
    subject: Presence[str]
    target_player: Presence[str]
    target_position: Presence[Position]
    player_action: Presence[str]
    reroll_of: Presence[Phase]
    turn: Presence[TurnContext]
    options: List[PromptOption]
    sufficiency: Literal["partial"] = "partial"


@dataclass(frozen=True)
class ObservationV1:
    schema_version: Literal[1]
    observer_team: Side
    geometry: Geometry
    players: List[PlayerObservation]
    teams: List[TeamObservation]
    balls: List[BallObservation]
    match: MatchObservation
    decision: DecisionContext

    def to_json(self):
        """Return fresh JSON-compatible containers with no engine references."""
        return asdict(self)


class ObservationControl:
    """Episode-local roster binding. Never pass this object to a policy.

    IDs depend only on initial home/away roster order, not UUIDs or jersey
    numbers. Retain the initial roster even when players leave the pitch or a
    roster list is reordered. A new Game (including a clone) needs its own
    binding. Dynamic roster additions are unsupported and fail explicitly.
    """

    def __init__(self, game):
        self._game = game
        self._teams = (game.state.home_team, game.state.away_team)
        self._rosters = tuple(tuple(team.players) for team in self._teams)
        self._team_ids = {team.team_id: side for team, side in zip(self._teams, ("home", "away"))}
        self._player_ids = {
            player.player_id: side + ":" + str(slot)
            for side, roster in zip(("home", "away"), self._rosters)
            for slot, player in enumerate(roster)
        }
        if len(self._team_ids) != 2 or len(self._player_ids) != sum(map(len, self._rosters)):
            raise ValueError("Observation binding requires unique roster identities")

    def _check(self, game):
        if (game is not self._game or game.state.home_team is not self._teams[0]
                or game.state.away_team is not self._teams[1]):
            raise ValueError("Observation binding belongs to a different episode")
        for team, roster in zip(self._teams, self._rosters):
            if any(not any(player is original for original in roster) for player in team.players):
                raise ValueError("Observation binding does not support roster additions")

    def _team(self, team):
        return None if team is None else self._team_ids[team.team_id]

    def _player(self, player):
        return None if player is None else self._player_ids[player.player_id]

    def internal_player_id(self, local_id):
        """Resolve an observation ID in control code; never a numeric feature."""
        for internal_id, candidate in self._player_ids.items():
            if candidate == local_id:
                return internal_id
        raise ValueError("Unknown episode-local player ID")


def _scalars(schema, source):
    # These dataclasses are explicit scalar allowlists, never engine __dict__.
    return schema(**{field.name: getattr(source, field.name) for field in fields(schema)})


def _names(values):
    return [value.name for value in values]


def _player_observation(control, team, slot, player):
    dugout = control._game.state.dugouts[team.team_id]
    location = "unplaced"
    if player.position is not None:
        location = "pitch"
    else:
        for field, label in (("reserves", "reserves"), ("kod", "ko"),
                             ("casualties", "casualties"), ("dungeon", "dungeon")):
            if player in getattr(dugout, field):
                location = label
                break
    return PlayerObservation(
        id=control._player(player), team=control._team(team), slot=slot,
        position=_position(player.position), location=location, role=player.role.name,
        attributes=Attributes(player.get_ma(), player.get_st(), player.get_ag(), player.get_av()),
        role_attributes=_scalars(Attributes, player.role),
        extra_attributes=Attributes(player.extra_ma, player.extra_st, player.extra_ag, player.extra_av),
        skills=_names(player.role.skills) + _names(player.extra_skills),
        used_skills=sorted(_names(player.state.used_skills)),
        injuries=_names(player.injuries), injuries_gained=_names(player.state.injuries_gained),
        mng=player.mng, status=_scalars(PlayerStatus, player.state),
        squares_moved=[Position(square.x, square.y) for square in player.state.squares_moved],
    )


# Exact class matches: an unknown/custom procedure never gets its name or
# arbitrary attributes exported. Labels are schema values, not a Python stack.
_PHASES = {
    proc.StartGame: "start_game", proc.CoinTossFlip: "coin_toss",
    proc.CoinTossKickReceive: "kick_receive", proc.Setup: "setup",
    proc.PlaceBall: "place_ball", proc.HighKick: "high_kick", proc.Touchback: "touchback",
    proc.Turn: "turn", proc.MoveAction: "move", proc.BlockAction: "block_action",
    proc.BlitzAction: "blitz", proc.PassAction: "pass_action",
    proc.HandoffAction: "handoff_action", proc.FoulAction: "foul_action",
    proc.Block: "block", proc.Push: "push", proc.FollowUp: "follow_up",
    proc.Reroll: "reroll", proc.GFI: "gfi", proc.Dodge: "dodge",
    proc.Pickup: "pickup", proc.Catch: "catch", proc.PassAttempt: "pass",
    proc.Interception: "interception", proc.Intercept: "intercept", proc.Apothecary: "apothecary",
}


def _participants(context):
    """Selected already-public participants/announced destination, no rolls."""
    kind = type(context)
    if kind in (proc.Block, proc.FollowUp):
        return context.attacker, context.defender, context.pos_to if kind is proc.FollowUp else None
    if kind is proc.Push:
        return context.pusher, context.player, None
    if kind in (proc.GFI, proc.Dodge):
        return context.player, None, context.position
    if kind is proc.PassAttempt:
        return context.passer, context.catcher, context.position
    if kind in (proc.MoveAction, proc.BlockAction, proc.BlitzAction, proc.PassAction,
                proc.HandoffAction, proc.FoulAction, proc.Pickup, proc.Catch,
                proc.Apothecary):
        return context.player, None, None
    if kind is proc.Intercept:
        return context.interceptor, context.passer, None
    if kind is proc.Interception:
        return context.passer, None, None
    return None, None, None


def _decision(game, control):
    state = game.state
    # A terminal state can retain engine procedure/action fields. Do not turn
    # those stale fields into a pending decision.
    top = state.stack.items[-1] if state.stack.items and not state.game_over else None
    phase = "terminal" if state.game_over else "unstarted" if top is None else _PHASES.get(type(top), "other")
    choices = [] if state.game_over else state.available_actions
    subject, target, position = _participants(top)
    reroll_of = None
    if type(top) is proc.Reroll:
        reroll_of = _PHASES.get(type(top.context), "other")
        _, target, position = _participants(top.context)
        subject = top.player
    turn = next((item for item in reversed(state.stack.items) if type(item) is proc.Turn), None)
    return DecisionContext(
        phase=phase, pending=any(not choice.disabled for choice in choices),
        # Match Game.active_team / actor semantics (first cached choice).
        actor_team=_present(control._team(choices[0].team) if choices else None),
        active_team=_present(control._team(state.current_team)),
        active_player=_present(control._player(state.active_player)),
        subject=_present(control._player(subject)), target_player=_present(control._player(target)),
        target_position=_position(position),
        player_action=_present(None if state.player_action_type is None else state.player_action_type.name),
        reroll_of=_present(reroll_of),
        turn=_present(_scalars(TurnContext, turn) if turn is not None and not state.game_over else None),
        options=[PromptOption(choice.action_type.name, _present(control._team(choice.team)),
                              _present(None if choice.skill is None else choice.skill.name), choice.disabled)
                 for choice in choices],
    )


def observe(game, control: ObservationControl, observer_team: Side) -> ObservationV1:
    """Read a public snapshot of the current engine boundary without refreshing it.

    Both observers receive the same public data in absolute arena coordinates;
    only observer_team differs. The caller owns advancement to a decision.
    """
    if observer_team not in ("home", "away"):
        raise ValueError("Observer team must be home or away")
    control._check(game)
    state = game.state
    arena = game.arena
    balls = []
    for ball in state.pitch.balls:
        carrier = None
        if ball.is_carried and ball.position is not None:
            x, y = ball.position.x, ball.position.y
            if 0 <= x < state.pitch.width and 0 <= y < state.pitch.height:
                carrier = control._player(state.pitch.board[y][x])
        balls.append(BallObservation(_position(ball.position), _present(carrier), ball.on_ground, ball.is_carried))
    return ObservationV1(
        schema_version=1, observer_team=observer_team,
        geometry=Geometry(arena.width, arena.height,
                          [[tile.name for tile in row] for row in arena.board],
                          [[tile != Tile.CROWD for tile in row] for row in arena.board]),
        players=[_player_observation(control, team, slot, player)
                 for team, roster in zip(control._teams, control._rosters)
                 for slot, player in enumerate(roster)],
        teams=[TeamObservation(control._team(team), team.race, team.state.score, team.state.turn,
                               _scalars(TeamResources, team.state)) for team in control._teams],
        balls=balls,
        match=MatchObservation(state.half, state.round, _present(None),
                               _present(control._team(state.kicking_this_drive)),
                               _present(control._team(state.receiving_this_drive)),
                               state.weather.name, state.game_over),
        decision=_decision(game, control),
    )
