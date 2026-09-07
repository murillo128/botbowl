"""Selected chain resolution, including upstream njustesen/botbowl#244."""
import pytest

from tests.util import *


def assert_board_references(game):
    on_board = []
    for y, row in enumerate(game.state.pitch.board):
        for x, player in enumerate(row):
            if player is not None:
                assert player.position == game.get_square(x, y)
                assert game.get_player(player.player_id) is player
                on_board.append(player.player_id)
    assert len(on_board) == len(set(on_board))
    for team in game.state.teams:
        for player in team.players:
            if player.position is not None:
                assert game.get_player_at(player.position) is player


def chain_game(length, direction, crowd=False, full=False):
    game, (attacker,) = get_custom_game_turn([(10, 8)])
    for team in game.state.teams:
        team.state.rerolls = 0
        team.state.apothecaries = 0
    if crowd:
        origin = length + 1 if direction == -1 else game.arena.height - length - 2
        point = lambda i, j=0: game.get_square(10 + j, origin + direction * i)
    else:
        point = lambda i, j=0: game.get_square(10 + direction * i, 8 + j)
    if attacker.position != point(0):
        game.move(attacker, point(0))
    pool = iter(game.get_opp_team(attacker.team).players + attacker.team.players[1:])
    chain = []
    for i in range(1, length + 1):
        player = next(pool)
        player.role.skills = []
        game.put(player, point(i))
        chain.append(player)
    occupied = {(i, j) for i in range(2, length + 1) for j in (-1, 1)}
    if full:
        occupied |= {(length + i, j) for i in (-1, 0, 1) for j in (-1, 0, 1)
                     if (i, j) != (0, 0) and (i, j) != (-1, 0)}
    for i, j in sorted(occupied):
        player = next(pool)
        player.role.skills = []
        player.state.up = False
        game.put(player, point(i, j))
    game.get_ball().move_to(chain[-1].position)
    game.get_ball().is_carried = True
    game.set_available_actions()
    return game, attacker, chain, point


def begin_chain(game, attacker, chain, knockdown=False):
    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=chain[0].position))
    game.step(Action(ActionType.SELECT_DEFENDER_DOWN if knockdown else ActionType.SELECT_PUSH))
    for player in chain[1:]:
        game.step(Action(ActionType.PUSH, position=player.position))
        assert_board_references(game)


@pytest.mark.parametrize('length', [2, 4])
@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('use', [False, True])
@pytest.mark.parametrize('knockdown', [False, True])
def test_chain_stand_firm(length, direction, use, knockdown):
    game, attacker, chain, point = chain_game(length, direction)
    chain[-1].extra_skills.append(Skill.STAND_FIRM)
    original = {p.player_id: p.position for t in game.state.teams for p in t.players}
    result = BBDieResult.DEFENDER_DOWN if knockdown else BBDieResult.PUSH
    with only_fixed_rolls(game, block_dice=[result] * abs(game.num_block_dice(attacker, chain[0])),
                         d6=[1, 1] if knockdown else []):
        begin_chain(game, attacker, chain, knockdown)
        assert all(p.position == original[p.player_id] for t in game.state.teams for p in t.players)
        assert game.get_available_actions()[0].team is chain[-1].team
        game.step(Action(ActionType.USE_SKILL if use else ActionType.DONT_USE_SKILL))
        if not use:
            game.step(Action(ActionType.PUSH, position=point(length + 1)))
            assert game.get_procedure().__class__ is FollowUp
            assert game.get_available_actions()[0].positions == [point(0), point(1)]
            assert_board_references(game)
            game.step(Action(ActionType.FOLLOW_UP, position=point(1)))
        assert_board_references(game)
        assert attacker.position == point(0 if use else 1)
        assert [p.position for p in chain] == [point(i + (0 if use else 1)) for i in range(1, length + 1)]
        assert chain[0].state.up is not knockdown
        assert game.get_ball_carrier() is chain[-1]
        assert game.get_ball().position == chain[-1].position
        assert isinstance(game.get_procedure(), Turn)
        assert not any(isinstance(p, (Push, FollowUp, Block, EndPlayerTurn)) for p in game.state.stack.items)
        assert game.state.active_player is None
        assert attacker.state.used
        assert sum(r.outcome_type == OutcomeType.END_PLAYER_TURN and r.player is attacker
                   for r in game.state.reports) == 1
        assert not any(attacker in a.players for a in game.get_available_actions())
        if use:
            assert not game.has_report_of_type(OutcomeType.FOLLOW_UP)


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('use', [False, True])
def test_chain_side_step_stand_firm(direction, full, use):
    game, attacker, chain, point = chain_game(2, direction, full=full)
    tail = chain[-1]
    tail.extra_skills.extend([Skill.SIDE_STEP, Skill.STAND_FIRM])
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] * abs(game.num_block_dice(attacker, chain[0]))):
        begin_chain(game, attacker, chain)
        game.step(Action(ActionType.USE_SKILL if use else ActionType.DONT_USE_SKILL))
        if not use:
            actions = game.get_available_actions()
            if full:
                assert set(actions[0].positions) == {point(3, -1), point(3), point(3, 1)}
                # Fully occupied Side Step falls back to ordinary chain geometry.
                next_player = game.get_player_at(point(3))
                game.step(Action(ActionType.PUSH, position=point(3)))
                game.step(Action(ActionType.PUSH, position=point(4)))
                assert next_player.position == point(4)
            else:
                assert actions[0].team is tail.team
                assert point(1, 1) in actions[0].positions  # outside ordinary push arc
                game.step(Action(ActionType.PUSH, position=point(1, 1)))
            game.step(Action(ActionType.FOLLOW_UP, position=point(0)))
        assert_board_references(game)
        assert game.get_ball_carrier() is tail
        assert game.get_ball().position == tail.position
        assert chain[0].position == point(1 if use else 2)
        assert attacker.position == point(0)
        assert isinstance(game.get_procedure(), Turn)
        assert sum(r.outcome_type == OutcomeType.END_PLAYER_TURN for r in game.state.reports) == 1


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('use', [False, True])
def test_chain_stand_firm_at_crowd(direction, use):
    game, attacker, chain, point = chain_game(4, direction, crowd=True)
    tail = chain[-1]
    tail.extra_skills.append(Skill.STAND_FIRM)
    # Keep the ball on the original defender, separating crowd removal from throw-in.
    game.get_ball().move_to(chain[0].position)
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH] * abs(game.num_block_dice(attacker, chain[0])),
                         d6=[] if use else [1, 1]):
        begin_chain(game, attacker, chain)
        game.step(Action(ActionType.USE_SKILL if use else ActionType.DONT_USE_SKILL))
        if not use:
            assert all(s.out_of_bounds for s in game.get_available_actions()[0].positions)
            game.step(Action(ActionType.PUSH, position=point(5)))
            game.step(Action(ActionType.FOLLOW_UP, position=point(1)))
            assert tail.position is None
            assert tail in game.get_reserves(tail.team)
        else:
            assert tail.position == point(4)
        assert_board_references(game)
        assert game.get_ball_carrier() is chain[0]
        assert game.get_ball().position == chain[0].position
        assert isinstance(game.get_procedure(), Turn)
        assert sum(r.outcome_type == OutcomeType.END_PLAYER_TURN for r in game.state.reports) == 1


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('rooted', [False, True])
@pytest.mark.parametrize('knockdown', [False, True])
def test_stopped_chain_drops_original_carriers_ball(direction, rooted, knockdown):
    game, attacker, chain, point = chain_game(2, direction)
    attacker.extra_skills.append(Skill.STRIP_BALL)
    if rooted:
        chain[-1].state.taken_root = True
    else:
        chain[-1].extra_skills.append(Skill.STAND_FIRM)
    game.get_ball().move_to(chain[0].position)
    result = BBDieResult.DEFENDER_DOWN if knockdown else BBDieResult.PUSH
    with only_fixed_rolls(game, block_dice=[result] * abs(game.num_block_dice(attacker, chain[0])),
                         d6=[1, 1] if knockdown else [], d8=[2]):
        begin_chain(game, attacker, chain, knockdown)
        if not rooted:
            game.step(Action(ActionType.USE_SKILL))
        assert_board_references(game)
        assert chain[0].position == point(1)
        assert attacker.position == point(0)
        assert game.get_ball().position == point(1, -1)
        assert not game.get_ball().is_carried
        assert not game.has_report_of_type(OutcomeType.FOLLOW_UP)
        assert not game.has_report_of_type(OutcomeType.PUSHED)
        assert game.has_report_of_type(OutcomeType.FUMBLE)
        assert isinstance(game.get_procedure(), Turn)
