"""Small, deterministic injury scenarios shared by the casualty regressions."""
from botbowl.core.model import ActionChoice
from botbowl.core.procedure import Injury
from botbowl.core.table import ActionType
from examples.a2c.a2c_env import A2C_Reward
from tests.util import get_custom_game_turn


def injury_game(apothecaries=0, skills=(), own=False):
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)])
    # get_custom_game_turn puts players on the pitch without taking them out of
    # reserves; keep compartments disjoint so these tests check real locations.
    for player in (attacker, defender):
        game.get_reserves(player.team).remove(player)
    victim = attacker if own else defender
    victim.team.state.apothecaries = apothecaries
    victim.extra_skills.extend(skills)
    victim.place_prone()
    return game, attacker, victim


def injure(game, victim, attacker):
    Injury(game, victim, inflictor=attacker)
    game.set_available_actions()
    game.step()


def assert_location(game, player, location):
    assert (player.position is not None) == (location == 'pitch')
    for name, players in [('reserves', game.get_reserves(player.team)),
                          ('ko', game.get_knocked_out(player.team)),
                          ('casualties', game.get_casualties(player.team))]:
        assert players.count(player) == int(name == location)


def assert_rewards(game, victim, amount):
    """Read the same report stream from both team perspectives, twice each."""
    actions = game.state.available_actions
    try:
        for team, expected in [(victim.team, -amount),
                               (game.get_opp_team(victim.team), amount)]:
            game.state.available_actions = [ActionChoice(ActionType.CONTINUE, team=team)]
            reward = A2C_Reward()
            assert reward(game) == expected
            assert reward(game) == 0.0
    finally:
        game.state.available_actions = actions
