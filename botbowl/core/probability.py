"""Immutable values for the bounded BB2016 probability-query experiments."""
from typing import Literal, NamedTuple, Tuple, Union

from .table import PassDistance, WeatherType


PassRerollPolicy = Literal['never', 'pass_skill', 'pass_skill_then_team']
BlockRerollPolicy = Literal['never', 'avoid_attacker_down']


class RerollProbabilityInfo(NamedTuple):
    policy: Union[PassRerollPolicy, BlockRerollPolicy]
    already_rerolled: bool
    pass_skill_available: bool
    team_reroll_available: bool
    source: Literal['none', 'pass', 'team']
    loner_success_probability: float
    replacement_probability: float
    pass_use_probability: float
    team_use_probability: float


class PassOutcomeProbabilities(NamedTuple):
    accurate: float
    inaccurate: float
    fumble: float
    ruleset_id: str
    conditions: str
    agility_target: int
    modifier: int
    pass_distance: PassDistance
    weather: WeatherType
    from_position: Tuple[int, int]
    target_position: Tuple[int, int]
    reroll: RerollProbabilityInfo


class BlockOutcomeProbabilities(NamedTuple):
    attacker_down: float
    defender_down: float
    attacker_ball_loss: float
    defender_ball_loss: float
    selected_face_probabilities: Tuple[float, float, float, float, float]
    ruleset_id: str
    conditions: str
    selection_policy: str
    signed_dice: int
    chooser: Literal['attacker', 'defender']
    reroll_team: Literal['attacker']
    blitz: bool
    attack_position: Tuple[int, int]
    reroll: RerollProbabilityInfo


__all__ = ['PassRerollPolicy', 'BlockRerollPolicy', 'RerollProbabilityInfo',
           'PassOutcomeProbabilities', 'BlockOutcomeProbabilities']
