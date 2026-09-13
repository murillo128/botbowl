"""Issue #28's fixed design tables and independent physical-roll enumerators.

Expectations predate the query implementation. Symbols and successive filters
are test-owned; no production probability/effect/selection helper is called.
"""
from collections import Counter
from fractions import Fraction as F
from itertools import product


# AG, horizontal range, sunny, active TZ count, Accurate, raw faces, never, once
PASS_ROWS = (
    (3, 3, False, 0, False, 'FIAAAA', ('2/3', '1/6', '1/6'), ('8/9', '1/18', '1/18')),
    (3, 6, False, 0, False, 'FIIAAA', ('1/2', '1/3', '1/6'), ('3/4', '1/6', '1/12')),
    (3, 9, False, 0, False, 'FFIIAA', ('1/3', '1/3', '1/3'), ('5/9', '2/9', '2/9')),
    (3, 12, False, 0, False, 'FFFIIA', ('1/6', '1/3', '1/2'), ('11/36', '5/18', '5/12')),
    (3, 12, True, 0, False, 'FFFFIA', ('1/6', '1/6', '2/3'), ('11/36', '5/36', '5/9')),
    (3, 3, False, 0, True, 'FAAAAA', ('5/6', '0', '1/6'), ('35/36', '0', '1/36')),
    (3, 12, True, 3, False, 'FFFFFA', ('1/6', '0', '5/6'), ('11/36', '0', '25/36')),
    (6, 9, False, 0, False, 'FAAAAA', ('5/6', '0', '1/6'), ('35/36', '0', '1/36')),
    (6, 12, False, 0, False, 'FFAAAA', ('2/3', '0', '1/3'), ('8/9', '0', '1/9')),
    (5, 9, False, 0, False, 'FFAAAA', ('2/3', '0', '1/3'), ('8/9', '0', '1/9')),
)

# Attacker Block, signed dice, selected counts, never A/D, avoid A/D.
BLOCK_ROWS = (
    (False, 1, (1, 1, 2, 1, 1), '1/3', '1/2', '1/9', '1/2'),
    (False, -1, (1, 1, 2, 1, 1), '1/3', '1/2', '1/9', '1/2'),
    (False, 2, (1, 3, 12, 9, 11), '1/9', '23/36', '1/81', '203/324'),
    (False, -2, (11, 5, 16, 1, 3), '4/9', '1/4', '16/81', '2/9'),
    (False, 3, (1, 7, 56, 61, 91), '1/27', '53/72', '1/729', '1421/1944'),
    (False, -3, (91, 19, 98, 1, 7), '55/108', '1/8', '3025/11664', '29/288'),
    (True, 1, (1, 1, 2, 1, 1), '1/6', '1/2', '1/36', '7/12'),
    (True, -1, (1, 1, 2, 1, 1), '1/6', '1/2', '1/36', '7/12'),
    (True, 2, (1, 7, 8, 9, 11), '1/36', '3/4', '1/1296', '37/48'),
    (True, -2, (11, 1, 16, 3, 5), '11/36', '1/4', '121/1296', '47/144'),
    (True, 3, (1, 37, 26, 61, 91), '1/216', '7/8', '1/46656', '1519/1728'),
    (True, -3, (91, 1, 98, 7, 19), '91/216', '1/8', '8281/46656', '307/1728'),
)
FACES = ('skull', 'both', 'push', 'push', 'stumble', 'pow')
SELECTED = ('skull', 'both', 'push', 'stumble', 'pow')


def pass_oracle(raw, source='none', loner=False):
    """Six faces, 36 pairs, or 216 triples, with actual source costs."""
    counts, replacements, uses, total = Counter(), 0, 0, 0
    for first, second, gate in product(raw, raw if source != 'none' else '-',
                                       range(1, 7) if loner else (6,)):
        trigger = first != 'A' and source != 'none'
        replace = trigger and gate >= 4
        counts[second if replace else first] += 1
        replacements += replace
        uses += trigger
        total += 1
    return tuple(F(counts[s], total) for s in 'AIF'), F(replacements, total), F(uses, total)


def block_resolver(a_block=False, d_block=False, dodge=False, tackle=False,
                   strip=False, sure_hands=False, carrier=None, crowd=False):
    def resolve(face):
        fallen, released = set(), set()
        if face == 'skull':
            fallen.add('a')
        elif face == 'both':
            if not a_block:
                fallen.add('a')
            if not d_block:
                fallen.add('d')
        else:
            if crowd or face == 'pow' or (face == 'stumble' and (not dodge or tackle)):
                fallen.add('d')
            if strip and not sure_hands and carrier == 'd':
                released.add('d')
        if carrier in fallen:
            released.add(carrier)
        return fallen, released
    return resolve


def select(roll, dice, resolve):
    chooser, opponent = ('a', 'd') if dice > 0 else ('d', 'a')
    candidates = [(face, *resolve(face)) for face in roll]
    for preferred in (lambda c: chooser not in c[1], lambda c: opponent in c[1],
                      lambda c: chooser not in c[2], lambda c: opponent in c[2]):
        candidates = [c for c in candidates if preferred(c)] or candidates
    return min(candidates, key=lambda c: ('pow', 'stumble', 'push', 'both', 'skull').index(c[0]))


def block_oracle(dice, reroll=False, loner=False, **effects):
    """Explicit original/replacement tuples, never a marginal transformation."""
    resolve = block_resolver(**effects)
    rolls = list(product(FACES, repeat=abs(dice)))
    # Reuse decisions, but count every physical tuple pair separately.
    decisions = {roll: select(roll, dice, resolve) for roll in rolls}
    counts, events, replacements, uses, total = Counter(), [0] * 4, 0, 0, 0
    for first, second, gate in product(rolls, rolls if reroll else [None],
                                       range(1, 7) if loner else (6,)):
        selected = decisions[first]
        trigger = reroll and 'a' in selected[1]
        replace = trigger and gate >= 4
        if replace:
            selected = decisions[second]
        face, fallen, released = selected
        counts[face] += 1
        for i, occurs in enumerate(('a' in fallen, 'd' in fallen, 'a' in released, 'd' in released)):
            events[i] += occurs
        replacements += replace
        uses += trigger
        total += 1
    return (tuple(F(counts[s], total) for s in SELECTED),
            tuple(F(c, total) for c in events), F(replacements, total), F(uses, total))
