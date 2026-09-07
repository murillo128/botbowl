# Chain pushes and Frenzy/Stab (#9)

## Rule interpretation recorded before implementation

The execution base is `1153797c2346fd2d6360e60e0af4c85bcc218a1b`.
`botbowl/data/config/gym-11.json` selects `BB2016`; the repository's
`docs/bot-bowl-iii.md` also identifies that ruleset. The relevant legacy skill
wording is in Games Workshop's [Competition Rules Pack](https://cdn.steamstatic.com/steam/apps/58520/manuals/Blood_Bowl_Competition_Rules.pdf?t=1678959819),
Frenzy (printed p. 65 / PDF p. 39), Stab and Stand Firm (printed p. 67 /
PDF p. 41), and the block-declaration FAQ (printed p. 77 / PDF p. 51).
This is the pre-2020 rule text underlying these implemented skills; the later
2020/Second Season FAQ is not an input to this change.

Interpretation of those skill texts together: Stand Firm stops the selected
chain, including predecessors; retain knockdown at the original square. No
follow-up enters an occupied square. Declining permits normal push resolution.
A qualifying first block permits one Frenzy attack against the same standing,
adjacent opponent. Stab may replace that attack, chosen before block dice or
Dauntless; an initial Stab never triggers Frenzy. Each Blitz attack costs one
movement, including GFI when necessary; no budget means no second attack. A
failed GFI prevents the attack. Stab ends the activation, including a Blitz,
and beats (not equals) armour; its injury ignores modifiers. A second ordinary
block permits remaining Blitz movement. These are implementation expectations
inferred from the cited rules, not a claim that PR #151 is rules authority.

## Original failures

Reproduced on the exact base with an isolated CPython 3.11.16 environment and
strict game-owned dice (`tests.util.only_fixed_rolls`, accepted #14):

- [Upstream #244](https://github.com/njustesen/botbowl/issues/244): a selected
  two-player chain ending in Stand Firm reaches `Game.move` with the destination
  still occupied and raises `AssertionError`.
- [Upstream PR #151](https://github.com/njustesen/botbowl/pull/151): after a first
  Frenzy push, `Block` immediately consumes second-block dice instead of offering
  Stab. A strict queue containing only the first block raises
  `ForcedRollExhausted`. The author's [material warning](https://github.com/njustesen/botbowl/pull/151#issuecomment-860107868)
  says that draft permits Stab against anyone; its body also says Blitz is
  missing and Throw Team Mate changes are accidentally included. None of that
  patch is imported.

Full baseline traces and the standalone reproducer are retained under
`/tmp/botbowl-issue9-evidence/` (`baseline-chain.log`, `baseline-frenzy.log`,
`reproduce.py`). Deterministic regression tests accompany the fixes.
