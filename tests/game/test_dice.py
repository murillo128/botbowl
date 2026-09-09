import pytest
from botbowl.core.model import D3, D6, D8, BBDie, DiceSource
from botbowl.core.table import BBDieResult
import numpy as np


@pytest.mark.parametrize("die", [D3, D6, D8, BBDie])
def test_d_die(die):
    results = []
    n = 6
    if die == D3:
        n = 3
    elif die == D8:
        n = 8
    elif die == BBDieResult:
        n = 5  # Two push results
    for i in range(100):
        rnd = np.random.RandomState(0)
        result = die(rnd).value
        if die == D3:
            assert result in [1, 2, 3]
        elif die == D6:
            assert result in [1, 2, 3, 4, 5, 6]
        elif die == D8:
            assert result in [1, 2, 3, 4, 5, 6, 7, 8]
        elif die == BBDie:
            assert result in [BBDieResult.ATTACKER_DOWN, BBDieResult.BOTH_DOWN, BBDieResult.PUSH, BBDieResult.DEFENDER_STUMBLES, BBDieResult.DEFENDER_DOWN]
        results.append(result)
        if len(results) == n:
            break
    assert len(results) == n


def test_d3_fixation():
    for seed in range(10):
        rnd = DiceSource(seed)
        rnd.fix(D3, 1)
        rnd.fix(D3, 2)
        rnd.fix(D3, 3)
        assert D3(rnd).value == 1
        assert D3(rnd).value == 2
        assert D3(rnd).value == 3
    with pytest.raises(ValueError):
        rnd.fix(D3, 0)
    with pytest.raises(ValueError):
        rnd.fix(D3, 4)


def test_d6_fixation():
    for seed in range(10):
        rnd = DiceSource(seed)
        rnd.fix(D6, 1)
        rnd.fix(D6, 2)
        rnd.fix(D6, 3)
        rnd.fix(D6, 4)
        rnd.fix(D6, 5)
        rnd.fix(D6, 6)
        assert D6(rnd).value == 1
        assert D6(rnd).value == 2
        assert D6(rnd).value == 3
        assert D6(rnd).value == 4
        assert D6(rnd).value == 5
        assert D6(rnd).value == 6
    with pytest.raises(ValueError):
        rnd.fix(D6, 0)
    with pytest.raises(ValueError):
        rnd.fix(D6, 7)


def test_d8_fixation():
    for seed in range(10):
        rnd = DiceSource(seed)
        rnd.fix(D8, 1)
        rnd.fix(D8, 2)
        rnd.fix(D8, 3)
        rnd.fix(D8, 4)
        rnd.fix(D8, 5)
        rnd.fix(D8, 6)
        rnd.fix(D8, 7)
        rnd.fix(D8, 8)
        assert D8(rnd).value == 1
        assert D8(rnd).value == 2
        assert D8(rnd).value == 3
        assert D8(rnd).value == 4
        assert D8(rnd).value == 5
        assert D8(rnd).value == 6
        assert D8(rnd).value == 7
        assert D8(rnd).value == 8
    with pytest.raises(ValueError):
        rnd.fix(D8, 0)
    with pytest.raises(ValueError):
        rnd.fix(D8, 9)


def test_bb_fixation():
    for seed in range(10):
        rnd = DiceSource(seed)
        rnd.fix(BBDie, BBDieResult.ATTACKER_DOWN)
        rnd.fix(BBDie, BBDieResult.BOTH_DOWN)
        rnd.fix(BBDie, BBDieResult.PUSH)
        rnd.fix(BBDie, BBDieResult.DEFENDER_STUMBLES)
        rnd.fix(BBDie, BBDieResult.DEFENDER_DOWN)
        assert BBDie(rnd).value == BBDieResult.ATTACKER_DOWN
        assert BBDie(rnd).value == BBDieResult.BOTH_DOWN
        assert BBDie(rnd).value == BBDieResult.PUSH
        assert BBDie(rnd).value == BBDieResult.DEFENDER_STUMBLES
        assert BBDie(rnd).value == BBDieResult.DEFENDER_DOWN
    with pytest.raises(ValueError):
        rnd.fix(BBDie, 1)
