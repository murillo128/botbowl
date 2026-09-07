import pytest
from botbowl.core.game import *
from tests.util import *


def test_frenzy_block():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    # get a player
    players = game.get_players_on_pitch(team, False, True)
    attacker = None
    defender = None
    for p in players:
        if p.team != team:
            continue
        attacker = p
        adjacent = game.get_adjacent_opponents(attacker)
        if len(adjacent) > 0:
            attacker.extra_skills.append(Skill.FRENZY)
            defender = adjacent[0]
            break
    defender_pos = Square(defender.position.x, defender.position.y)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.DONT_USE_REROLL))
    game.step(Action(ActionType.SELECT_PUSH))
    game.step(Action(ActionType.PUSH, position=game.get_available_actions()[0].positions[0]))
    assert attacker.position == defender_pos
    defender_pos = Square(defender.position.x, defender.position.y)
    assert game.state.active_player is attacker
    game.step(Action(ActionType.DONT_USE_REROLL))
    game.step(Action(ActionType.SELECT_PUSH))
    game.step(Action(ActionType.PUSH, position=game.get_available_actions()[0].positions[0]))
    assert attacker.position == defender_pos
    assert game.state.active_player is not attacker


def test_frenzy_blitz():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    # get a player
    players = game.get_players_on_pitch(team, False, True)
    attacker = None
    defender = None
    for p in players:
        if p.team != team:
            continue
        attacker = p
        adjacent = game.get_adjacent_opponents(attacker)
        if len(adjacent) > 0:
            attacker.extra_skills.append(Skill.FRENZY)
            defender = adjacent[0]
            break
    defender_pos = Square(defender.position.x, defender.position.y)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.dice.fix(BBDie, BBDieResult.PUSH)
    game.step(Action(ActionType.START_BLITZ, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.DONT_USE_REROLL))
    game.step(Action(ActionType.SELECT_PUSH))
    game.step(Action(ActionType.PUSH, position=game.get_available_actions()[0].positions[0]))
    assert attacker.position == defender_pos
    defender_pos = Square(defender.position.x, defender.position.y)
    assert game.state.active_player is attacker
    game.step(Action(ActionType.DONT_USE_REROLL))
    game.step(Action(ActionType.SELECT_PUSH))
    game.step(Action(ActionType.PUSH, position=game.get_available_actions()[0].positions[0]))
    assert attacker.position == defender_pos
    assert game.state.active_player is attacker


def test_frenzy_knocked_down():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    # get a player
    players = game.get_players_on_pitch(team, False, True)
    attacker = None
    defender = None
    for p in players:
        if p.team != team:
            continue
        attacker = p
        adjacent = game.get_adjacent_opponents(attacker)
        if len(adjacent) > 0:
            attacker.extra_skills.append(Skill.FRENZY)
            defender = adjacent[0]
            break
    defender_pos = Square(defender.position.x, defender.position.y)
    game.dice.fix(BBDie, BBDieResult.DEFENDER_DOWN)
    game.dice.fix(BBDie, BBDieResult.DEFENDER_DOWN)
    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.DONT_USE_REROLL))
    game.step(Action(ActionType.SELECT_DEFENDER_DOWN))
    game.step(Action(ActionType.PUSH, position=game.get_available_actions()[0].positions[0]))
    assert attacker.position == defender_pos
    assert game.state.active_player is not attacker


def frenzy_game(blitz=False, moves=0, stab=True, defender_skills=()):
    game, (attacker, defender) = get_custom_game_turn([(5, 8)], [(6, 8)], ball_position=(2, 2))
    for team in game.state.teams:
        team.state.rerolls = 0
        team.state.apothecaries = 0
    attacker.extra_skills.append(Skill.FRENZY)
    if stab:
        attacker.extra_skills.append(Skill.STAB)
    defender.extra_skills.extend(defender_skills)
    game.step(Action(ActionType.START_BLITZ if blitz else ActionType.START_BLOCK, player=attacker))
    attacker.state.moves = moves
    game.state.clocks.clear()
    return game, attacker, defender


def first_frenzy_push(game, defender):
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_PUSH))
    game.step(Action(ActionType.PUSH, position=Square(7, 8)))


def attack_reports(game, outcome, player=None, skill=None):
    return [r for r in game.state.reports if r.outcome_type == outcome
            and (player is None or r.player is player) and (skill is None or r.skill == skill)]


@pytest.mark.parametrize('blitz', [False, True])
@pytest.mark.parametrize('second', [ActionType.BLOCK, ActionType.STAB])
def test_frenzy_second_attack_same_opponent(blitz, second):
    game, attacker, defender = frenzy_game(blitz)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
        first_frenzy_push(game, defender)
        assert len(attack_reports(game, OutcomeType.BLOCK_ROLL)) == 1
        actions = game.get_available_actions()
        assert {a.action_type for a in actions} == {ActionType.BLOCK, ActionType.STAB}
        assert all(a.positions == [defender.position] and a.team is attacker.team for a in actions)
        assert attacker.position == Square(6, 8)
        assert attacker.state.moves == (1 if blitz else 0)
        # An unrelated adjacent opponent cannot become the second target (#151).
        other = next(p for p in defender.team.players if p.position is None)
        game.put(other, Square(6, 7))
        game.set_available_actions()
        game.state.clocks.clear()
        before = game.to_json()
        with pytest.raises(InvalidActionError):
            game.step(Action(ActionType.STAB, position=other.position))
        assert game.to_json() == before
        with pytest.raises(InvalidActionError):
            game.step(Action(ActionType.END_PLAYER_TURN))
        game.remove(other)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] if second == ActionType.BLOCK else [],
                         d6=[1, 1] if second == ActionType.STAB else []):
        game.step(Action(second, position=defender.position))
        if second == ActionType.BLOCK:
            game.step(Action(ActionType.SELECT_PUSH))
            game.step(Action(ActionType.PUSH, position=Square(8, 8)))
        assert len(attack_reports(game, OutcomeType.BLOCK_ROLL)) == (2 if second == ActionType.BLOCK else 1)
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)) == (second == ActionType.STAB)
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.FRENZY)) == 1
        if blitz and second == ActionType.BLOCK:
            assert game.state.active_player is attacker
            assert attacker.state.moves == 2
            assert attacker.state.has_blocked
            assert not any(a.action_type in (ActionType.BLOCK, ActionType.STAB) for a in game.get_available_actions())
            game.step(Action(ActionType.END_PLAYER_TURN))
        assert isinstance(game.get_procedure(), Turn)
        assert game.state.active_player is None
        assert attacker.state.used
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1


@pytest.mark.parametrize('stab', [False, True])
@pytest.mark.parametrize('moves', [0, 5, 6, 7])
def test_frenzy_blitz_second_block_movement_budget(moves, stab):
    game, attacker, defender = frenzy_game(blitz=True, moves=moves, stab=stab)
    assert attacker.get_ma() == 6
    allowed = moves < 7
    gfi_count = int(moves >= 6) + int(allowed and moves >= 5)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] * (2 if allowed else 1), d6=[2] * gfi_count):
        first_frenzy_push(game, defender)
        if allowed:
            if stab:
                game.step(Action(ActionType.BLOCK, position=defender.position))
            assert attacker.state.moves == moves + 2
            game.step(Action(ActionType.SELECT_PUSH))
            game.step(Action(ActionType.PUSH, position=Square(8, 8)))
        if moves < 6:
            assert attacker.state.moves == moves + 2
        assert len(attack_reports(game, OutcomeType.SUCCESSFUL_GFI)) == gfi_count
        assert len(attack_reports(game, OutcomeType.BLOCK_ROLL)) == (2 if allowed else 1)
        assert attacker.position == Square(7 if allowed else 6, 8)
        assert not any(a.action_type in (ActionType.BLOCK, ActionType.STAB) for a in game.get_available_actions())
        if moves < 6:
            assert attacker.state.has_blocked
            assert game.state.active_player is attacker
            game.step(Action(ActionType.END_PLAYER_TURN))
        else:
            # Existing action handling ends automatically when movement is exhausted.
            assert game.state.active_player is None
            assert attacker.state.used
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1


@pytest.mark.parametrize('second', [ActionType.BLOCK, ActionType.STAB])
@pytest.mark.parametrize('failure', [False, True])
def test_frenzy_second_attack_gfi(second, failure):
    game, attacker, defender = frenzy_game(blitz=True, moves=5)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
        first_frenzy_push(game, defender)
    # Failed GFI uses only fall armour, and never rolls the attack.
    d6 = [1, 1, 1] if failure else [2] + ([1, 1] if second == ActionType.STAB else [])
    with only_fixed_rolls(game, d6=d6,
                         block_dice=[BBDieResult.PUSH] if not failure and second == ActionType.BLOCK else []):
        game.step(Action(second, position=defender.position))
        if failure:
            assert not attacker.state.up
            assert defender.state.up
            assert game.has_report_of_type(OutcomeType.TURNOVER)
            assert len(attack_reports(game, OutcomeType.BLOCK_ROLL)) == 1
            assert not attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)
            assert game.state.current_team is defender.team
            assert game.state.active_player is None
            assert not any(isinstance(p, (Block, Stab, BlitzAction)) for p in game.state.stack.items)
        elif second == ActionType.BLOCK:
            assert attacker.state.moves == 7
            game.step(Action(ActionType.SELECT_PUSH))
            game.step(Action(ActionType.PUSH, position=Square(8, 8)))
            assert attacker.state.moves == 7
        else:
            assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1
            assert isinstance(game.get_procedure(), Turn)


@pytest.mark.parametrize('reason', ['fend', 'knockdown', 'both_down', 'foul_appearance'])
@pytest.mark.parametrize('stab', [False, True])
def test_frenzy_ineligible_second_attack_never_spends_gfi(reason, stab):
    skills = {'fend': [Skill.FEND], 'knockdown': [], 'both_down': [Skill.BLOCK],
              'foul_appearance': [Skill.FOUL_APPEARANCE]}[reason]
    game, attacker, defender = frenzy_game(blitz=True, moves=6, stab=stab, defender_skills=skills)
    if reason == 'both_down':
        attacker.extra_skills.append(Skill.BLOCK)
    die, choice = {'fend': (BBDieResult.PUSH, ActionType.SELECT_PUSH),
                   'knockdown': (BBDieResult.DEFENDER_DOWN, ActionType.SELECT_DEFENDER_DOWN),
                   'both_down': (BBDieResult.BOTH_DOWN, ActionType.SELECT_BOTH_DOWN),
                   'foul_appearance': (None, None)}[reason]
    d6 = [2] + ([1, 1] if reason == 'knockdown' else [1] if reason == 'foul_appearance' else [])
    with only_fixed_rolls(game, block_dice=[] if die is None else [die], d6=d6):
        game.step(Action(ActionType.BLOCK, position=defender.position))
        if choice is not None:
            game.step(Action(choice))
            if reason != 'both_down':
                game.step(Action(ActionType.PUSH, position=Square(7, 8)))
        assert game.state.active_player is attacker
        assert attacker.state.moves == 7
        assert len(attack_reports(game, OutcomeType.SUCCESSFUL_GFI)) == 1
        assert not attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.FRENZY)
        assert not any(a.action_type in (ActionType.BLOCK, ActionType.STAB) for a in game.get_available_actions())


@pytest.mark.parametrize('blitz', [False, True])
def test_frenzy_stand_firm_still_allows_second_attack(blitz):
    game, attacker, defender = frenzy_game(blitz, defender_skills=[Skill.STAND_FIRM])
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH], d6=[1, 1]):
        game.step(Action(ActionType.BLOCK, position=defender.position))
        game.step(Action(ActionType.SELECT_PUSH))
        game.step(Action(ActionType.USE_SKILL))
        assert attacker.position == Square(5, 8)
        assert defender.position == Square(6, 8)
        assert all(a.positions == [defender.position] for a in game.get_available_actions())
        game.step(Action(ActionType.STAB, position=defender.position))
        assert not game.has_report_of_type(OutcomeType.FOLLOW_UP)
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1


def test_frenzy_attack_choice_uses_existing_proc_bot_player_action():
    from botbowl.ai.proc_bot import ProcBot

    class StabBot(ProcBot):
        def player_action(self, game):
            choice = next(a for a in game.get_available_actions() if a.action_type == ActionType.STAB)
            return Action(ActionType.STAB, position=choice.positions[0])

    game, attacker, defender = frenzy_game(blitz=True)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH], d6=[1, 1]):
        first_frenzy_push(game, defender)
        game.step(StabBot('stab').act(game))
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1


@pytest.mark.parametrize('blitz', [False, True])
def test_frenzy_cannot_change_to_stab_after_second_block_is_declared(blitz):
    game, attacker, defender = frenzy_game(blitz)
    attacker.extra_skills.append(Skill.DAUNTLESS)
    defender.extra_st = 1
    # Both attacks fail Dauntless and roll two uphill dice.
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] * 4, d6=[1, 1]):
        first_frenzy_push(game, defender)
        game.step(Action(ActionType.BLOCK, position=defender.position))
        game.state.clocks.clear()
        before = game.to_json()
        with pytest.raises(InvalidActionError):
            game.step(Action(ActionType.STAB, position=defender.position))
        assert game.to_json() == before
        game.step(Action(ActionType.SELECT_PUSH))
        game.step(Action(ActionType.PUSH, position=Square(8, 8)))
        assert len(attack_reports(game, OutcomeType.BLOCK_ROLL)) == 2
        assert not attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)


@pytest.mark.parametrize('pathfinding', [False, True])
def test_initial_frenzy_block_failed_gfi_cancels_both_attacks(pathfinding):
    game, attacker, defender = frenzy_game(blitz=True, moves=6)
    game.config.pathfinding_enabled = pathfinding
    game.set_available_actions()
    with only_fixed_rolls(game, d6=[1, 1, 1]):
        game.step(Action(ActionType.BLOCK, position=defender.position))
        assert not attacker.state.up
        assert defender.state.up
        assert game.state.current_team is defender.team
        assert not attack_reports(game, OutcomeType.BLOCK_ROLL)
        assert not attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)
        assert not any(isinstance(p, (Block, Stab, Frenzy, BlitzAction)) for p in game.state.stack.items)


@pytest.mark.parametrize('blitz', [False, True])
@pytest.mark.parametrize('direction', [-1, 1])
def test_frenzy_chain_stopped_by_stand_firm_has_no_followup(blitz, direction):
    from tests.game.test_push import chain_game, assert_board_references
    game, attacker, chain, point = chain_game(4, direction)
    attacker.extra_skills.extend([Skill.FRENZY, Skill.STAB])
    chain[-1].extra_skills.append(Skill.STAND_FIRM)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] * abs(game.num_block_dice(attacker, chain[0])),
                         d6=[1, 1]):
        game.step(Action(ActionType.START_BLITZ if blitz else ActionType.START_BLOCK, player=attacker))
        game.step(Action(ActionType.BLOCK, position=chain[0].position))
        game.step(Action(ActionType.SELECT_PUSH))
        for player in chain[1:]:
            game.step(Action(ActionType.PUSH, position=player.position))
        game.step(Action(ActionType.USE_SKILL))
        assert_board_references(game)
        assert attacker.position == point(0)
        assert [p.position for p in chain] == [point(i) for i in range(1, 5)]
        assert all(a.positions == [chain[0].position] for a in game.get_available_actions())
        game.step(Action(ActionType.STAB, position=chain[0].position))
        assert not game.has_report_of_type(OutcomeType.FOLLOW_UP)
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1
        assert game.get_ball_carrier() is chain[-1]
        assert_board_references(game)


@pytest.mark.parametrize('second', [ActionType.BLOCK, ActionType.STAB])
def test_frenzy_continuation_revert_and_replay(second):
    from tests.game.test_push import assert_board_references
    game, attacker, defender = frenzy_game(blitz=True)
    game.enable_forward_model()
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
        first_frenzy_push(game, defender)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] if second == ActionType.BLOCK else [],
                         d6=[1, 1] if second == ActionType.STAB else []):
        before = game.to_json()
        checkpoint = game.get_step()
        game.step(Action(second, position=defender.position))
        if second == ActionType.BLOCK:
            game.step(Action(ActionType.SELECT_PUSH))
            game.step(Action(ActionType.PUSH, position=Square(8, 8)))
        after = game.to_json()
        changes = game.revert(checkpoint)
        assert game.to_json() == before
        assert_board_references(game)
        game.forward(changes)
        assert game.to_json() == after
        assert_board_references(game)
