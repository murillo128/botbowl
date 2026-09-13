"""Compatible mechanism pairs, with every artificial entry state labelled."""
import botbowl as bb
from botbowl.core.procedure import Apothecary, Reroll, Setup, Shadowing, Turn
from tests.baseline import progress_action
from tests.interaction_corpus import fresh


def micro(case, side, seed, pathfinding=False, ogre=False):
    probe = fresh(11, seed, pathfinding, turns=2, ogre=ogre, case=case, side=side,
                  origin='synthetic', fixture='two placed roster players on a cleared gym-11 pitch')
    game = probe.game
    while type(game.get_procedure()) is not Turn or game.active_team.team_id != side:
        probe.step(progress_action(game))
    game.clear_board()
    team = game.active_team
    player = next(p for p in team.players if p.role.name == ('Ogre' if ogre else 'Lineman'))
    opponent = next(p for p in game.get_opp_team(team).players if p.role.name == 'Lineman')
    for participant, xy in ((player, (5, 5)), (opponent, (6, 5))):
        game.get_reserves(participant.team).remove(participant)
        game.put(participant, game.get_square(*xy))
    for t in game.state.teams:
        t.state.rerolls = 0
        t.state.apothecaries = 0
    game.state.weather = bb.WeatherType.NICE
    game.get_ball().move_to(game.get_square(3, 3))
    game.get_ball().is_carried = False
    game.set_available_actions()
    probe.config['fixture_at_decision'] = len(probe.actions)
    probe.fixture('clear pitch; place participants; reset rerolls, Apothecaries, weather and ball')
    return probe, player, opponent


def movement(side='home', seed=0, pathfinding=False, variant='team'):
    """GFI reroll -> real dodge -> opponent Shadowing -> pickup/continuation."""
    probe, player, opponent = micro('movement', side, seed, pathfinding)
    with probe.evidence():
        game = probe.game
        probe.config['variant'] = variant
        opponent.extra_skills.append(bb.Skill.SHADOWING)  # legal General advancement
        if variant == 'sure_feet':
            player.extra_skills.append(bb.Skill.SURE_FEET)  # legal double on a lineman
        else:
            player.team.state.rerolls = 1
        game.get_ball().move_to(game.get_square(4, 5))
        probe.fixture('compatible movement skills, team reroll and pickup square')
        probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
        if pathfinding:
            assert any(choice.paths for choice in game.get_available_actions()), 'pathfinder was not exercised'
        player.state.moves = player.get_ma()  # synthetic: enter the GFI boundary
        game.set_available_actions()
        probe.fixture('synthetic GFI movement count or scorer placement; refresh choices')
        with probe.dice(d6=[1, 2, 6] if variant == 'sure_feet' else [1]):
            probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(4, 5)))
        if variant != 'sure_feet':
            assert isinstance(game.get_procedure(), Reroll)
            with probe.dice(d6=[2, 6]):
                probe.step(bb.Action(bb.ActionType.USE_REROLL))
            assert player.team.state.rerolls == 0
        assert isinstance(game.get_procedure(), Shadowing)
        assert game.actor is game.get_team_agent(opponent.team)
        accept = variant != 'decline'
        with probe.dice(d6=[2, 2, 6] if accept else [6]):
            probe.step(bb.Action(bb.ActionType.USE_SKILL if accept else bb.ActionType.DONT_USE_SKILL,
                                 player=opponent))
            probe.step(bb.Action(bb.ActionType.END_PLAYER_TURN))
        assert player.position == game.get_square(4, 5)
        assert opponent.position == game.get_square(5 if accept else 6, 5)
        assert game.get_ball().is_carried and game.get_ball().position == player.position
        assert game.has_report_of_type(bb.OutcomeType.SUCCESSFUL_PICKUP)
        assert type(game.get_procedure()) is Turn and game.state.active_player is None
        return probe


def push(side='home', seed=0, variant='accept'):
    """Strip Ball against Sure Hands + Stand Firm (legal G/S advancements)."""
    probe, attacker, defender = micro('push', side, seed)
    with probe.evidence():
        game = probe.game
        probe.config['variant'] = variant
        attacker.extra_skills.append(bb.Skill.STRIP_BALL)
        defender.extra_skills.extend([bb.Skill.SURE_HANDS, bb.Skill.STAND_FIRM])
        game.get_ball().move_to(defender.position)
        game.get_ball().is_carried = True
        probe.fixture('Strip Ball versus Sure Hands and Stand Firm; carried ball')
        accept = variant == 'accept'
        with probe.dice(block_dice=[bb.BBDieResult.PUSH]):
            probe.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
            probe.step(bb.Action(bb.ActionType.BLOCK, position=defender.position))
            probe.step(bb.Action(bb.ActionType.SELECT_PUSH))
            assert game.actor is game.get_team_agent(defender.team)
            probe.step(bb.Action(bb.ActionType.USE_SKILL if accept else bb.ActionType.DONT_USE_SKILL,
                                 player=defender))
            if not accept:
                probe.step(bb.Action(bb.ActionType.PUSH, position=game.get_square(7, 5)))
                probe.step(bb.Action(bb.ActionType.FOLLOW_UP, position=game.get_square(6, 5)))
        assert defender.position == game.get_square(6 if accept else 7, 5)
        assert game.get_ball().is_carried and game.get_ball().position == defender.position
        assert type(game.get_procedure()) is Turn and game.state.active_player is None
        return probe


def injury(side='home', seed=0, variant='use'):
    """A block reaches Mighty Blow/Thick Skull injury and an opponent Apothecary."""
    probe, attacker, defender = micro('injury', side, seed)
    with probe.evidence():
        game = probe.game
        probe.config['variant'] = variant
        attacker.extra_skills.append(bb.Skill.MIGHTY_BLOW)
        defender.extra_skills.append(bb.Skill.THICK_SKULL)
        defender.team.state.apothecaries = 1
        probe.fixture('Mighty Blow versus Thick Skull; one defending Apothecary')
        use = variant == 'use'
        # Armour 12 leaves Mighty Blow available: injury 8 + 1 - Thick Skull 1 is KO.
        with probe.dice(block_dice=[bb.BBDieResult.DEFENDER_DOWN], d6=[6, 6, 4, 4]):
            probe.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
            probe.step(bb.Action(bb.ActionType.BLOCK, position=defender.position))
            probe.step(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
            probe.step(bb.Action(bb.ActionType.PUSH, position=game.get_square(7, 5)))
            probe.step(bb.Action(bb.ActionType.FOLLOW_UP, position=game.get_square(5, 5)))
            assert isinstance(game.get_procedure(), Apothecary)
            assert game.actor is game.get_team_agent(defender.team)
            probe.step(bb.Action(bb.ActionType.USE_APOTHECARY if use else bb.ActionType.DONT_USE_APOTHECARY))
        assert defender.team.state.apothecaries == int(not use)
        assert defender.state.stunned == use and defender.state.knocked_out != use
        assert (defender in game.get_knocked_out(defender.team)) != use
        assert type(game.get_procedure()) is Turn
        return probe


def negatrait(side='home', seed=0, variant='success'):
    """Roster Ogre's Bone Head/Loner, team reroll, then a real scored drive boundary."""
    probe, player, opponent = micro('negatrait', side, seed, ogre=True)
    with probe.evidence():
        game = probe.game
        probe.config['variant'] = variant
        assert player.has_skill(bb.Skill.BONE_HEAD) and player.has_skill(bb.Skill.LONER)
        player.team.state.rerolls = 1
        probe.fixture('BB2016 Ogre Bone Head/Loner; one team reroll')
        success = variant == 'success'
        with probe.dice(d6=[1, 4, 6] if success else [1, 3]):
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            assert isinstance(game.get_procedure(), Reroll)
            probe.step(bb.Action(bb.ActionType.USE_REROLL))
            if success:
                probe.step(bb.Action(bb.ActionType.END_PLAYER_TURN))
        assert player.state.bone_headed != success and player.team.state.rerolls == 0
        # Synthetic scorer placement; MOVE itself enters the real touchdown/clear/setup stack.
        scorer = next(p for p in player.team.players if p.role.name == 'Lineman' and p.position is None)
        touchdown_x = 1 if game.is_team_side(game.get_square(game.arena.width - 2, 5), player.team) else game.arena.width - 2
        before_x = touchdown_x + (1 if touchdown_x == 1 else -1)
        game.get_reserves(scorer.team).remove(scorer)
        game.put(scorer, game.get_square(before_x, 8))
        game.get_ball().move_to(scorer.position)
        game.get_ball().is_carried = True
        game.set_available_actions()
        probe.fixture('synthetic GFI movement count or scorer placement; refresh choices')
        with probe.dice():
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=scorer))
            probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(touchdown_x, 8)))
        assert isinstance(game.get_procedure(), Setup)
        assert player.team.state.score == 1
        assert not player.state.bone_headed and not player.state.used
        assert player.position is None and player in game.get_reserves(player.team)
        return probe
