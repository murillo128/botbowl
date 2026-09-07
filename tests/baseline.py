"""Small deterministic test scenarios; this is not a public game/session API."""
from contextlib import contextmanager
from dataclasses import dataclass, field

import botbowl as bb
from botbowl.core.procedure import Turn

SIZES = (1, 3, 5, 7, 11)
SEEDS = (0, 17)


def _square(position):
    return None if position is None else (position.x, position.y)


def _team(game, team):
    if team is None:
        return None
    return "home" if team is game.state.home_team else "away"


def _player(game, player):
    return None if player is None else (_team(game, player.team), player.nr)


def semantic_action(game, action):
    return (action.action_type.name, _player(game, action.player), _square(action.position))


def semantic_reports(game, start=0):
    """Preserve rule events, dice and participants, excluding generated IDs/time."""
    return [dict(event=report.outcome_type.name,
                 player=_player(game, report.player),
                 opponent=_player(game, report.opp_player),
                 team=_team(game, report.team), position=_square(report.position),
                 rolls=[roll.to_json() for roll in report.rolls], n=report.n,
                 skill=None if report.skill is None else report.skill.name)
            for report in game.state.reports[start:]]


@dataclass
class Scenario:
    game: bb.Game
    seed: int
    size: int
    max_steps: int = 256
    actions: list = field(default_factory=list)

    def diagnostic(self):
        return (f"size={self.size} seed={self.seed} steps={len(self.actions)} "
                f"procedure={type(self.game.state.stack.items[-1]).__name__ if self.game.state.stack.items else 'terminal'} "
                f"last_actions={self.actions[-12:]}")

    def step(self, action):
        assert len(self.actions) < self.max_steps, self.diagnostic()
        self.actions.append(semantic_action(self.game, action))
        try:
            self.game.step(action)
        except Exception as error:
            raise AssertionError(self.diagnostic()) from error

    def until(self, predicate):
        while not predicate(self.game):
            assert not self.game.state.game_over, self.diagnostic()
            try:
                action = progress_action(self.game)
            except Exception as error:
                raise AssertionError(self.diagnostic()) from error
            self.step(action)

    def trace(self):
        return {"actions": list(self.actions), "events": semantic_reports(self.game)}


def progress_action(game):
    """Deterministic setup and end-turn policy for bounded lifecycle probes."""
    choices = {choice.action_type: choice for choice in game.get_available_actions()}
    priorities = (bb.ActionType.START_GAME, bb.ActionType.HEADS, bb.ActionType.RECEIVE,
                  bb.ActionType.END_SETUP, bb.ActionType.SETUP_FORMATION_SPREAD,
                  bb.ActionType.SETUP_FORMATION_WEDGE, bb.ActionType.END_TURN)
    for action_type in priorities:
        if action_type not in choices:
            continue
        if action_type == bb.ActionType.END_SETUP and not game.is_setup_legal(game.active_team):
            continue
        return bb.Action(action_type)
    assert choices, "No available action before terminal state"
    choice = next(iter(choices.values()))
    return bb.Action(choice.action_type,
                     player=choice.players[0] if choice.players else None,
                     position=choice.positions[0] if choice.positions else None)


@contextmanager
def scenario(size=11, seed=0, turns=8, max_steps=256):
    """Fresh size-matched rosters; never changes the inherited cached helpers.

    Fixed dice are process-global in the inherited engine. Restore all queues even
    after failure so these micropositions do not contaminate unrelated tests.
    """
    dice = (bb.D3, bb.D6, bb.D8, bb.BBDie)
    saved = [die.FixedRolls for die in dice]
    try:
        for die in dice:
            die.FixedRolls = []
        config = bb.load_config(f"gym-{size}")
        config.kick_off_table = False
        config.pathfinding_enabled = False
        config.rounds = turns
        rules = bb.load_rule_set(config.ruleset)
        home = bb.load_team_by_filename("human", rules, board_size=size)
        away = bb.load_team_by_filename("human", rules, board_size=size)
        game = bb.Game("baseline", home, away, bb.Agent("home", human=True),
                       bb.Agent("away", human=True), config, seed=seed)
        probe = Scenario(game, seed, size, max_steps)
        game.init()
        probe.until(lambda g: type(g.get_procedure()) is Turn)
        yield probe
    finally:
        for die, fixed in zip(dice, saved):
            die.FixedRolls = fixed


def place_players(probe, own, opponents=(), ball=None):
    """Place only named participants, independent of the setup formation.

    Micropositions may exceed the one-player pitch count to isolate a pass on
    size 1. They exercise procedure geometry, not legal roster/setup counts.
    """
    game = probe.game
    game.clear_board()
    placed = []
    for team, positions in ((game.active_team, own), (game.get_opp_team(game.active_team), opponents)):
        assert len(positions) <= len(team.players), probe.diagnostic()
        for player, coordinates in zip(team.players, positions):
            position = game.get_square(*coordinates)
            assert not game.is_out_of_bounds(position), probe.diagnostic()
            assert game.get_player_at(position) is None, probe.diagnostic()
            game.put(player, position)
            placed.append(player)
    game.state.weather = bb.WeatherType.NICE
    for team in game.state.teams:
        team.state.rerolls = 0
    game.get_ball().is_carried = ball is not None
    game.get_ball().move_to(game.get_square(*(ball if ball is not None else (1, 1))))
    game.set_available_actions()
    game.state.reports.clear()
    return tuple(placed)
