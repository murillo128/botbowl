"""Shared feature/configuration definitions, independent of optional Gym APIs."""
from typing import Iterable, List, Optional, Union
import botbowl.core.procedure as procedures
from botbowl.ai.layers import *
from botbowl.core.model import *
from botbowl.core import load_config, load_arena, load_formation

formation_defaults = {1: ['def_spread.txt', 'def_zone.txt', 'off_line.txt', 'off_wedge.txt'],
                      3: ['def_spread.txt', 'off_wedge.txt'],
                      5: ['def_spread.txt', 'off_wedge.txt'],
                      7: ['def_spread.txt', 'off_wedge.txt'],
                      11: ['def_spread.txt', 'def_zone.txt', 'off_line.txt', 'off_wedge.txt']
                      }


class EnvConf:
    config: Configuration
    simple_action_types: List[Union[ActionType, Formation]]
    positional_action_types: List[ActionType]
    action_types: List[Union[ActionType, Formation]]
    layers: List[FeatureLayer]
    procedures: List[procedures.Procedure]
    formations: List[Formation]

    def __init__(self, size=11,
                 extra_formations: Optional[Iterable[Formation]] = None,
                 extra_feature_layers: Optional[Iterable[FeatureLayer]] = None,
                 pathfinding=False):

        self.size = size
        self.config: Configuration = load_config(f"gym-{size}")
        self.config.pathfinding_enabled = pathfinding

        self.simple_action_types = [
            ActionType.START_GAME,
            ActionType.HEADS,
            ActionType.TAILS,
            ActionType.KICK,
            ActionType.RECEIVE,
            ActionType.END_PLAYER_TURN,
            ActionType.USE_REROLL,
            ActionType.DONT_USE_REROLL,
            ActionType.USE_SKILL,
            ActionType.DONT_USE_SKILL,
            ActionType.END_TURN,
            ActionType.STAND_UP,
            ActionType.SELECT_ATTACKER_DOWN,
            ActionType.SELECT_BOTH_DOWN,
            ActionType.SELECT_PUSH,
            ActionType.SELECT_DEFENDER_STUMBLES,
            ActionType.SELECT_DEFENDER_DOWN,
            ActionType.SELECT_NONE,
            ActionType.USE_BRIBE,
            ActionType.DONT_USE_BRIBE,
        ]
        self.formations = [load_formation(formation, size=size) for formation in formation_defaults[size]]
        if extra_formations is not None:
            extra_formations = list(extra_formations)
            if not all(isinstance(formation, Formation) for formation in extra_formations):
                raise TypeError("extra_formations must contain Formation instances")
            self.formations.extend(extra_formations)
        arena = load_arena(self.config.arena)
        for formation in self.formations:
            for home in (False, True):
                formation.validate(arena, self.config, home=home)
        self.simple_action_types.extend(self.formations)

        self.positional_action_types = [
            ActionType.PLACE_BALL,
            ActionType.PUSH,
            ActionType.FOLLOW_UP,
            ActionType.MOVE,
            ActionType.BLOCK,
            ActionType.PASS,
            ActionType.FOUL,
            ActionType.HANDOFF,
            ActionType.LEAP,
            ActionType.STAB,
            ActionType.SELECT_PLAYER,
            ActionType.START_MOVE,
            ActionType.START_BLOCK,
            ActionType.START_BLITZ,
            ActionType.START_PASS,
            ActionType.START_FOUL,
            ActionType.START_HANDOFF
        ]

        self.action_types = self.simple_action_types + self.positional_action_types

        self.layers = [AvailablePositionLayer(action_type) for action_type in self.positional_action_types]
        self.layers.extend([
            OccupiedLayer(),
            OwnPlayerLayer(),
            OppPlayerLayer(),
            OwnTackleZoneLayer(),
            OppTackleZoneLayer(),
            UpLayer(),
            StunnedLayer(),
            UsedLayer(),
            RollProbabilityLayer(),
            BlockDiceLayer(),
            ActivePlayerLayer(),
            TargetPlayerLayer(),
            MALayer(),
            STLayer(),
            AGLayer(),
            AVLayer(),
            MovementLeftLayer(),
            GFIsLeftLayer(),
            BallLayer(),
            OwnHalfLayer(),
            OwnTouchdownLayer(),
            OppTouchdownLayer(),
            SkillLayer(Skill.BLOCK),
            SkillLayer(Skill.DODGE),
            SkillLayer(Skill.SURE_HANDS),
            SkillLayer(Skill.CATCH),
            SkillLayer(Skill.PASS)
        ])
        if extra_feature_layers is not None:
            self.layers.extend(extra_feature_layers)

        # Procedures that require actions
        self.procedures = [
            procedures.StartGame,
            procedures.CoinTossFlip,
            procedures.CoinTossKickReceive,
            procedures.Setup,
            procedures.PlaceBall,
            procedures.HighKick,
            procedures.Touchback,
            procedures.Turn,
            procedures.MoveAction,
            procedures.BlockAction,
            procedures.BlitzAction,
            procedures.PassAction,
            procedures.HandoffAction,
            procedures.FoulAction,
            procedures.ThrowBombAction,
            procedures.Block,
            procedures.Push,
            procedures.FollowUp,
            procedures.Apothecary,
            procedures.PassAttempt,
            procedures.Interception,
            procedures.Reroll,
            procedures.Ejection]

