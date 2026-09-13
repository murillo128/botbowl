"""Targeted failing evidence for #25, routed to #9 (Push) and #12 (injury).

Run explicitly: pytest tests/issue25/reproduce_push_injury.py -q -s
This is a synthetic two-player position with legal BB2016 advancements. The
Both Down control reaches the same injury with its inflictor intact. No engine
patch, xfail or assertion of the defective result is applied here.
"""
import json

import pytest

import botbowl as bb
from botbowl.core.procedure import Apothecary, Turn
from tests.baseline import progress_action, semantic_reports
from tests.util import only_fixed_rolls


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('route', ['push', 'stand_firm', 'both_down_control'])
def test_knockdown_preserves_mighty_blow_inflictor(side, route):
    config = bb.load_config('gym-11')
    config.rounds = 2
    config.pathfinding_enabled = False
    rules = bb.load_rule_set(config.ruleset)
    teams = [bb.load_team_by_filename('human', rules) for _ in range(2)]
    for name, team in zip(('home', 'away'), teams):
        team.team_id = name
        for index, player in enumerate(team.players):
            player.player_id = '{}-{}'.format(name, index)
    game = bb.Game('issue25-push-injury', *teams,
                   bb.Agent('home', human=True), bb.Agent('away', human=True),
                   config, seed=0, external_control=True)
    game.init()
    setup_actions = []
    while type(game.get_procedure()) is not Turn or game.active_team.team_id != side:
        assert len(setup_actions) < 40, 'bounded setup did not reach requested turn'
        action = progress_action(game)
        setup_actions.append(action.to_json())
        game.advance(action, max_steps=2000)

    # Synthetic fixture starts here. No invalid setup or impossible skill mix:
    # Block is a General advancement; Mighty Blow/Thick Skull/Stand Firm can be
    # acquired by Human linemen on doubles. Compartments remain disjoint.
    game.clear_board()
    attacker, defender = [next(p for p in team.players if p.role.name == 'Lineman')
                          for team in (game.active_team, game.get_opp_team(game.active_team))]
    for player, xy in ((attacker, (5, 5)), (defender, (6, 5))):
        game.get_reserves(player.team).remove(player)
        game.put(player, game.get_square(*xy))
    attacker.extra_skills.extend([bb.Skill.BLOCK, bb.Skill.MIGHTY_BLOW])
    defender.extra_skills.append(bb.Skill.THICK_SKULL)
    if route == 'stand_firm':
        defender.extra_skills.append(bb.Skill.STAND_FIRM)
    for team in game.state.teams:
        team.state.rerolls = 0
        team.state.apothecaries = int(team is defender.team)
    game.state.weather = bb.WeatherType.NICE
    game.get_ball().move_to(game.get_square(3, 3))
    game.get_ball().is_carried = False
    game.set_available_actions()
    game.state.reports.clear()

    control = route == 'both_down_control'
    die = bb.BBDieResult.BOTH_DOWN if control else bb.BBDieResult.DEFENDER_DOWN
    actions = [bb.Action(bb.ActionType.START_BLOCK, player=attacker),
               bb.Action(bb.ActionType.BLOCK, position=defender.position),
               bb.Action(bb.ActionType.SELECT_BOTH_DOWN if control else bb.ActionType.SELECT_DEFENDER_DOWN)]
    if route == 'push':
        actions += [bb.Action(bb.ActionType.PUSH, position=game.get_square(7, 5)),
                    bb.Action(bb.ActionType.FOLLOW_UP, position=attacker.position)]
    elif route == 'stand_firm':
        actions += [bb.Action(bb.ActionType.USE_SKILL, player=defender)]

    # Armour 12 does not need Mighty Blow; injury 8 + MB 1 - Thick Skull 1 is KO.
    # Losing the inflictor silently changes this to 8 - 1 = 7, hence STUNNED.
    with only_fixed_rolls(game, block_dice=[die], d6=[6, 6, 4, 4]):
        for action in actions:
            assert game.validate_action(action).allowed
            game.advance(action, max_steps=2000)
    from botbowl.core import pathfinding
    reproduction = dict(seed=0, config='gym-11', ruleset=config.ruleset, rounds=2,
                        pathfinding=False, kick_off_table=config.kick_off_table,
                        backend=pathfinding.get_safest_path.__module__, origin='synthetic',
                        side=side, route=route, setup_actions=setup_actions,
                        actions=[a.to_json() for a in actions],
                        forced=dict(block_dice=[die.name], d6=[6, 6, 4, 4]),
                        events=semantic_reports(game), procedures=game.get_procedure_names(),
                        options=[a.to_json() for a in game.get_available_actions()])
    print(json.dumps(reproduction, sort_keys=True))
    assert isinstance(game.get_procedure(), Apothecary), (
        'Expected KO/Apothecary: Push dropped the Mighty Blow inflictor; ' + json.dumps(reproduction))
    assert game.actor is game.get_team_agent(defender.team)
    assert defender.team.state.apothecaries == 1
