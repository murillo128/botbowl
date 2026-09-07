"""Bounded issue-20 investigation; no production correction or hidden xfail.

Set BOTBOWL_ISSUE20_EVIDENCE to retain decisions and first-divergence snapshots.
The clean control is independently constructed and never enables trajectory.
"""
from copy import deepcopy
from enum import Enum
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
from types import FunctionType

import numpy as np
import pytest

import botbowl as bb
from botbowl.core.procedure import MoveAction, Procedure, Turn


def fields(value, class_names=None):
    # Enabling trajectory copies public class defaults onto instances. Compare
    # their values in both games, independent of where the attribute is stored.
    cls = type(value)
    if (class_names is None or cls.__dir__ is not object.__dir__
            or cls.__getattribute__ is not object.__getattribute__):
        names = dir(value)
    else:
        if cls not in class_names:
            definitions = {key: item for base in reversed(cls.__mro__) for key, item in vars(base).items()}
            class_names[cls] = [key for key, item in definitions.items()
                                if not key.startswith("_") and key != "game"
                                and not isinstance(item, FunctionType)]
        # Plain attribute lookup reads instance storage directly, with descriptors
        # and inherited defaults resolved below. Instance overrides of methods
        # remain visible. Custom lookup/dir implementations use the general path.
        result = {key: item for key, item in vars(value).items()
                  if not key.startswith("_") and key != "game" and not callable(item)} if hasattr(value, "__dict__") else {}
        for key in class_names[cls]:
            item = getattr(value, key)
            if callable(item):
                result.pop(key, None)
            else:
                result[key] = item
        return result
    result = {}
    for key in names:
        if not key.startswith("_") and key != "game":
            item = getattr(value, key)
            if not callable(item):
                result[key] = item
    return result


@lru_cache(maxsize=128)
def string_keys(keys):
    """Cache only immutable string-key ordering, never observed values."""
    return tuple(sorted(keys, key=lambda key: repr(key) + ", "))


def semantic(game):
    """Compare public state, including procedure internals omitted by to_json.

    Normalize roster references, container implementations and immutable squares.
    Ignore trajectory bookkeeping and clocks as required by the #14 contract.
    Path caches are represented by their complete public path result.
    """
    stack = game.state.stack.items
    # Only field names are reused, and only during this observation. Every value
    # (including nested rules and class defaults) is read again on the next call.
    class_names = {}

    def public(value):
        return fields(value, class_names)

    scalar_types = {str, int, float, bool, type(None)}
    handlers, enum_tokens = {}, {}

    def encode(value):
        cls = type(value)
        if cls in scalar_types:
            return value
        if cls not in handlers:
            handlers[cls] = next(handler for kind, handler in categories if isinstance(value, kind))
        return handlers[cls](value)

    def dictionary(value):
        if all(type(key) is str for key in value):
            return [[key, encode(value[key])] for key in string_keys(tuple(value))]
        # Preserve key sets and the old full-pair repr order when normalized keys
        # tie (for example a Square and a tuple with the same encoded contents).
        groups = {}
        for key, item in value.items():
            pair = [encode(key), encode(item)]
            groups.setdefault(repr(pair[0]) + ", ", []).append(pair)
        return [pair for key in sorted(groups)
                for pair in (groups[key] if len(groups[key]) == 1 else sorted(groups[key], key=repr))]

    def enumeration(value):
        identity = id(value)
        if identity not in enum_tokens:
            enum_tokens[identity] = [type(value).__name__, value.name]
        return enum_tokens[identity]

    def player(value):
        assert game.state.player_by_id[value.player_id] is value, "foreign player reference"
        return ["player", value.player_id]

    def team(value):
        assert game.state.team_by_id[value.team_id] is value, "foreign team reference"
        return ["team", value.team_id]

    def procedure(value):
        assert value.game is game, "foreign procedure game"
        for index, proc in enumerate(stack):
            if value is proc:
                return ["procedure", index, type(value).__name__]
        return [type(value).__name__, encode(public(value))]

    def other(value):
        if type(value).__name__ == "Path":
            return ["Path", encode({name: getattr(value, name) for name in
                    ("steps", "rolls", "prob", "block_dice", "handoff_roll", "foul_roll")})]
        return [type(value).__name__, encode(public(value))]

    # Cache dispatch by type for this observation, never object values. Keep the
    # original precedence for numpy scalar conversion and reference validation.
    categories = (
        (np.ndarray, lambda value: encode(value.tolist())),
        (np.generic, lambda value: encode(value.item())),
        ((str, int, float, bool), lambda value: value),
        (Enum, enumeration),
        (bb.Player, player),
        (bb.Team, team),
        (bb.Square, lambda value: ["square", value.x, value.y]),
        (Procedure, procedure),
        (dict, dictionary),
        ((list, tuple), lambda value: [encode(item) for item in value]),
        (set, lambda value: sorted([encode(item) for item in value], key=repr)),
        (object, other),
    )

    state = public(game.state)
    state.pop("clocks")
    state.pop("stack")
    # References in state are tokens; include their complete public definitions too.
    return {
        "state": encode(state),
        "ruleset": encode(game.ruleset),
        "teams": [encode(public(team)) for team in game.state.teams],
        "players": [encode(public(player)) for team in game.state.teams for player in team.players],
        "stack": [[type(proc).__name__, encode(public(proc))] for proc in stack],
    }


def mutable_graph(root):
    """Map reachable mutable identities, including rules and trajectory history."""
    found, seen = {}, set()

    def visit(value, path):
        if value is None or isinstance(value, (str, bytes, int, float, bool, Enum, np.generic, type)) or callable(value):
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, (tuple, frozenset)):
            for index, item in enumerate(value):
                visit(item, "{}[{}]".format(path, index))
            return
        found[id(value)] = path
        if isinstance(value, dict):
            for key, item in value.items():
                visit(key, path + ".key")
                visit(item, "{}[{}]".format(path, key))
        elif isinstance(value, (list, set, np.ndarray)):
            for index, item in enumerate(value):
                visit(item, "{}[{}]".format(path, index))
        else:
            for key, item in vars(value).items() if hasattr(value, "__dict__") else ():
                visit(item, path + "." + key)

    visit(root, "game")
    return found


def independent(*games):
    graphs = [mutable_graph(game) for game in games]
    for index, left in enumerate(graphs):
        for right in graphs[index + 1:]:
            shared = left.keys() & right.keys()
            assert not shared, "shared mutable objects: {}".format(
                sorted((left[key], right[key]) for key in shared)[:10])


def consistency(game):
    """Identity and board/position checks are independent of state equivalence."""
    seen = set()
    for y, row in enumerate(game.state.pitch.board):
        for x, player in enumerate(row):
            if player is not None:
                assert game.state.player_by_id[player.player_id] is player
                assert player.player_id not in seen, "duplicate board occupant"
                seen.add(player.player_id)
                assert player.position is game.get_square(x, y), "board/position mismatch"
    for team in game.state.teams:
        assert game.state.team_by_id[team.team_id] is team
        for player in team.players:
            assert game.state.player_by_id[player.player_id] is player
            assert game.state.team_by_player_id[player.player_id] is team
            assert player.team is team
            if player.position is not None:
                assert game.get_player_at(player.position) is player
            else:
                assert player.player_id not in seen
    for proc in game.state.stack.items:
        assert proc.game is game
    ball_refs = list(game.state.pitch.balls)
    for proc in game.state.stack.items:
        for key in ("ball", "piece"):
            value = getattr(proc, key, None)
            if isinstance(value, bb.Ball):
                ball_refs.append(value)
    if ball_refs:
        assert all(ball is ball_refs[0] for ball in ball_refs), "split ball references"
    if game.is_quick_snap():
        assert game.state.current_team is game.get_receiving_team()
        assert game.active_team is game.get_receiving_team()
        active = game.state.active_player
        if active is not None:
            assert active.position is not None, "active Quick Snap player has no position"
            assert game.state.player_by_id[active.player_id] is active
            assert isinstance(game.get_procedure(), MoveAction)
            assert game.get_procedure().player is active
        for choice in game.state.available_actions:
            if choice.action_type == bb.ActionType.START_MOVE:
                expected = [p for p in game.get_players_on_pitch(game.get_receiving_team())
                            if not p.state.used and not p.state.taken_root]
                assert choice.players == expected
                assert all(p.position is not None for p in choice.players)
            if choice.action_type == bb.ActionType.MOVE:
                assert active is not None and active.state.moves == 0
                assert set(choice.positions) == set(game.get_adjacent_squares(active.position, occupied=False))
                assert not choice.paths and all(not rolls for rolls in choice.rolls)
    # Return the complete observation so callers can compare it without walking
    # the same unchanged state twice. No snapshot survives a game transition.
    return semantic(game)


def decision(action_type, player=None, position=None):
    return (action_type.name, player.player_id if player is not None else None,
            (position.x, position.y) if position is not None else None)


def action_for(game, spec):
    name, player_id, xy = spec
    return bb.Action(bb.ActionType[name],
                     player=game.state.player_by_id[player_id] if player_id is not None else None,
                     position=game.get_square(*xy) if xy is not None else None)


def play(game, spec):
    action = action_for(game, spec)
    assert game.validate_action(action).allowed, spec
    game.step(action)
    return consistency(game)


@lru_cache(maxsize=None)
def rules_blueprint(name):
    # Run the stock XML parser in a private namespace: earlier loader calls must
    # not contribute accumulated definitions. Do not patch its module or defaults.
    def empty_ruleset(name):
        return bb.RuleSet(name, races=[], star_players=[], inducements=[],
                          spp_actions={}, spp_levels={}, improvements={})

    loader = bb.load_rule_set
    isolated_loader = FunctionType(loader.__code__, dict(loader.__globals__, RuleSet=empty_ruleset),
                                   argdefs=loader.__defaults__, closure=loader.__closure__)
    # Nested model definitions also have mutable defaults. Detach those too;
    # games receive further copies, never the blueprint or parser-owned objects.
    return deepcopy(isolated_loader(name))


def kickoff_fixture(size=3, receiving=0, pathfinding=False, seed=0, forward=True):
    """Reach PlaceBall through legal setup decisions on the stock small arenas."""
    config = bb.load_config("gym-{}".format(size))
    config.pathfinding_enabled = pathfinding
    rules = deepcopy(rules_blueprint(config.ruleset))
    teams = [bb.load_team_by_filename("human", rules, board_size=size) for _ in range(2)]
    for side, team in enumerate(teams):
        team.team_id = "team-{}".format(side)
        for player in team.players:
            player.player_id = "{}-{}".format(side, player.nr)
    game = bb.Game("quick-snap", *teams, bb.Agent("home", human=True, agent_id="home"),
                   bb.Agent("away", human=True, agent_id="away"), config, seed=seed, ruleset=rules)
    game.init()
    play(game, decision(bb.ActionType.START_GAME))
    play(game, decision(bb.ActionType.HEADS))
    receive = game.active_team is game.state.teams[receiving]
    play(game, decision(bb.ActionType.RECEIVE if receive else bb.ActionType.KICK))
    for formation in (bb.ActionType.SETUP_FORMATION_SPREAD, bb.ActionType.SETUP_FORMATION_WEDGE):
        team = game.active_team
        play(game, decision(formation))
        assert game.is_setup_legal(team)
        assert len(game.get_players_on_pitch(team)) == size
        play(game, decision(bb.ActionType.END_SETUP))
    assert game.get_receiving_team() is game.state.teams[receiving]
    assert game.state.available_actions[0].action_type == bb.ActionType.PLACE_BALL
    # Real KickoffTable/Turn stack; forced event only, natural RNG resumes afterward.
    game.dice.fix(bb.D6, *([1, 4, 5] if size == 1 else [4, 5]))
    if size != 1:
        game.dice.fix(bb.D3, 1)
    game.dice.fix(bb.D8, 2)
    if forward:
        game.enable_forward_model()
    return game


def ball_decision(game):
    # Kick to a central unoccupied square on the receiving side.
    positions = game.state.available_actions[0].positions
    empty = [p for p in positions if game.get_player_at(p) is None]
    target = min(empty, key=lambda p: (abs(p.y - game.arena.height // 2),
                                      abs(p.x - game.arena.width // 2), p.x, p.y))
    return decision(bb.ActionType.PLACE_BALL, position=target)


def choices(game):
    result = []
    for choice in game.state.available_actions:
        if choice.disabled:
            continue
        for player in choice.players or [None]:
            for position in choice.positions or [None]:
                result.append(decision(choice.action_type, player, position))
    return result


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


class Investigation:
    def __init__(self, size, receiving, pathfinding, seed):
        self.config = dict(size=size, receiving=receiving, pathfinding=pathfinding, seed=seed)
        self.game = kickoff_fixture(**self.config)
        self.clean = kickoff_fixture(**self.config, forward=False)
        independent(self.game, self.clean)
        self.events = []
        self.failure = None
        self.initial = semantic(self.game)
        self.check("fixture")

    def advance(self, spec):
        # Retain the attempted decision even if the engine raises before check().
        self.events.append(dict(transition="attempt-advance", decision=spec, step=self.game.get_step()))
        return play(self.game, spec)

    def check(self, transition, expected=None, rng=None, spec=None, actual=None):
        if actual is None:
            actual = consistency(self.game)
        clean = consistency(self.clean)
        if expected is None:
            expected = clean
        if rng is None:
            rng = self.clean.capture_rng_state()
        self.events.append(dict(transition=transition, decision=spec, step=self.game.get_step(),
                                state_sha256=digest(actual)))
        try:
            assert actual == expected, transition
            assert self.game.capture_rng_state() == rng, transition + " RNG"
        except Exception:
            self.failure = dict(transition=transition, expected=expected, actual=actual)
            raise

    def round_trip(self, spec, commit=True):
        before = semantic(self.game)
        checkpoint = self.game.capture_checkpoint()
        stack_refs = tuple(self.game.state.stack.items)
        actions_ref = self.game.state.available_actions
        players = tuple(self.game.state.player_by_id.values())
        # A separate fresh game is used for each alternative, replaying committed decisions.
        control = kickoff_fixture(**self.config, forward=False)
        independent(self.game, self.clean, control)
        for old in self.committed:
            play(control, old)
        expected = play(control, spec)
        final_rng = control.capture_rng_state()
        for repeat in range(3):
            actual = self.advance(spec)
            # play() has just checked this complete state; no game transition
            # occurs before check() consumes it. The clean game is still observed.
            self.check("advance-{}".format(repeat), expected, final_rng, spec, actual)
            steps = self.game.revert(checkpoint.step)
            # Legacy undo retains final RNG; explicitly compare against that contract.
            self.check("revert-{}".format(repeat), before, final_rng, spec)
            assert all(a is b for a, b in zip(stack_refs, self.game.state.stack.items))
            assert self.game.state.available_actions is actions_ref
            assert all(self.game.state.player_by_id[p.player_id] is p for p in players)
            self.game.forward(steps)
            self.check("forward-{}".format(repeat), expected, final_rng, spec)
            self.game.restore_checkpoint(checkpoint)
            self.check("restore-{}".format(repeat), before, checkpoint.rng_state, spec)
        if commit:
            self.advance(spec)
            play(self.clean, spec)
            self.committed.append(spec)
            self.check("commit", spec=spec)
        independent(self.game, self.clean, control)

    def queries(self):
        before = semantic(self.game)
        rng = self.game.capture_rng_state()
        # Query the public choice list and regenerate choices twice. Quick Snap uses
        # adjacency even with pathfinding enabled. Never mutate returned choices.
        for _ in range(2):
            assert self.game.get_available_actions() is self.game.state.available_actions
            self.game.set_available_actions()
            self.check("query", before, rng)

    def run(self):
        self.committed = []
        root = self.game.capture_checkpoint()
        root_state = semantic(self.game)
        self.round_trip(ball_decision(self.game))
        assert self.game.is_quick_snap()
        assert self.game.has_report_of_type(bb.OutcomeType.KICKOFF_QUICK_SNAP)
        policy = random.Random(self.config["seed"] + 1000)
        for _ in range(24):
            if not self.game.is_quick_snap():
                break
            self.queries()
            options = choices(self.game)
            # All root player choices, all single-square targets, END_PLAYER_TURN,
            # UNDO and END_TURN are explored as alternatives before committing.
            for spec in options:
                self.round_trip(spec, commit=False)
            moves = [spec for spec in options if spec[0] in ("START_MOVE", "MOVE")]
            endings = [spec for spec in options if spec[0] in ("END_PLAYER_TURN", "END_TURN")]
            self.round_trip(policy.choice(moves or endings))
        else:
            raise AssertionError("Quick Snap decision budget exhausted")
        assert not self.game.is_quick_snap()
        # Complete any kickoff touchback decision and exercise the first ordinary
        # activation, where the enabled pathfinding backend really is used.
        for _ in range(3):
            if isinstance(self.game.get_procedure(), Turn):
                break
            self.round_trip(choices(self.game)[0])
        assert isinstance(self.game.get_procedure(), Turn)
        start = next(spec for spec in choices(self.game) if spec[0] == "START_MOVE")
        self.round_trip(start)
        if self.config["pathfinding"]:
            assert any(choice.paths for choice in self.game.state.available_actions)
        final = semantic(self.game)
        final_rng = self.game.capture_rng_state()
        for repeat in range(3):
            steps = self.game.revert(root.step)
            self.check("sequence-revert", root_state, final_rng)
            self.game.forward(steps)
            self.check("sequence-forward", final, final_rng)
            self.game.restore_checkpoint(root)
            self.check("sequence-restore", root_state, root.rng_state)
            self.clean = kickoff_fixture(**self.config, forward=False)
            independent(self.game, self.clean)
            for spec in self.committed:
                self.advance(spec)
                play(self.clean, spec)
                self.check("sequence-replay-{}".format(repeat), spec=spec)
            independent(self.game, self.clean)

    def save(self):
        directory = os.environ.get("BOTBOWL_ISSUE20_EVIDENCE")
        if directory:
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            name = "size{size}-side{receiving}-pf{pathfinding}-seed{seed}.json".format(**self.config)
            (target / name).write_text(json.dumps(dict(config=self.config, initial=self.initial,
                                                      committed=self.committed, events=self.events,
                                                      failure=self.failure), sort_keys=True, indent=2))


def test_rules_blueprint_is_bounded_after_ordinary_loader_calls():
    # Exercise cold construction after ordinary loaders, even if another test
    # already warmed the cache. Keep the inherited loader's state intact afterward.
    inherited = bb.RuleSet("loader-defaults")
    lists = (inherited.races, inherited.star_players, inherited.inducements)
    lengths = [len(items) for items in lists]
    dictionaries = (inherited.spp_actions, inherited.spp_levels, inherited.improvements)
    saved = [dict(items) for items in dictionaries]
    name = bb.load_config("gym-1").ruleset
    keys = ("races", "star_players", "inducements", "spp_actions", "spp_levels", "improvements")
    try:
        for _ in range(3):
            bb.load_rule_set(name)
        # Distinct extra entries also challenge contents, not just repeated counts.
        for items in dictionaries:
            items["issue-20-loader-sentinel"] = -1
        rules_blueprint.cache_clear()
        first = kickoff_fixture(size=1)
        assert [len(getattr(first.ruleset, key)) for key in keys] == [24, 71, 8, 5, 7, 6]
        before, rng = semantic(first), first.capture_rng_state()
        blueprint = rules_blueprint(name)
        independent(first, blueprint, inherited)
        bb.load_rule_set(name)
        cached = kickoff_fixture(size=1, forward=False)
        rules_blueprint.cache_clear()
        cold = kickoff_fixture(size=1, forward=False)
        assert semantic(first) == semantic(cached) == semantic(cold) == before
        assert first.capture_rng_state() == rng
        independent(first, cached, cold, blueprint, rules_blueprint(name), inherited)
    finally:
        rules_blueprint.cache_clear()
        for items, length in zip(lists, lengths):
            del items[length:]
        for items, original in zip(dictionaries, saved):
            items.clear()
            items.update(original)


@pytest.mark.parametrize("size", [1, 3])
@pytest.mark.parametrize("receiving", [0, 1])
@pytest.mark.parametrize("pathfinding", [False, True])
@pytest.mark.parametrize("seed", [0, 3, 17])
def test_quick_snap_advance_revert_forward_clean_equivalence(size, receiving, pathfinding, seed):
    investigation = Investigation(size, receiving, pathfinding, seed)
    try:
        investigation.run()
    except Exception as error:
        if investigation.failure is None:
            investigation.failure = dict(error=repr(error),
                stack=investigation.game.get_procedure_names(),
                positions=[decision(bb.ActionType.PLACE_PLAYER, p, p.position)
                           for team in investigation.game.state.teams for p in team.players])
        raise
    finally:
        investigation.save()


@pytest.mark.parametrize("receiving", [0, 1])
@pytest.mark.parametrize("pathfinding", [False, True])
def test_stale_action_rejected_and_detached_player_resolved(receiving, pathfinding):
    game = kickoff_fixture(receiving=receiving, pathfinding=pathfinding)
    play(game, ball_decision(game))
    root = game.capture_checkpoint()
    spec = next(spec for spec in choices(game) if spec[0] == "START_MOVE")
    detached = deepcopy(action_for(game, spec))
    canonical = game.state.player_by_id[spec[1]]
    assert detached.player is not canonical
    game.step(detached)
    consistency(game)
    assert game.state.active_player is canonical
    assert detached.player is not canonical  # The caller's object was not rewritten.
    before = semantic(game)
    rng = game.capture_rng_state()
    assert not game.validate_action(detached).allowed
    with pytest.raises(bb.InvalidActionError, match="not currently available"):
        game.step(detached)
    assert semantic(game) == before and game.capture_rng_state() == rng
    game.restore_checkpoint(root)
    consistency(game)
    assert game.validate_action(detached).allowed
    game.step(detached)
    assert semantic(game) == before


def test_observer_detects_corruption_and_equal_length_key_changes():
    game = kickoff_fixture()
    play(game, ball_decision(game))
    play(game, next(spec for spec in choices(game) if spec[0] == "START_MOVE"))
    before = semantic(game)
    player = game.state.active_player
    position = player.position
    player.position = None  # Deliberate consumer corruption; observer must fail.
    with pytest.raises(AssertionError, match="board/position mismatch"):
        consistency(game)
    player.position = position
    assert semantic(game) == before
    foreign = deepcopy(player)
    game.state.pitch.board[position.y][position.x] = foreign
    with pytest.raises(AssertionError):
        consistency(game)
    game.state.pitch.board[position.y][position.x] = player
    proc = game.get_procedure()
    proc.paths = {bb.Square(1, 2): 1}
    first = semantic(game)
    proc.paths = {bb.Square(2, 1): 1}
    assert semantic(game) != first  # No compare_iterable KeyError blind spot.


@pytest.mark.parametrize("container", [
    "races", "star_players", "inducements", "spp_actions", "spp_levels", "improvements",
])
def test_fixture_rules_are_equivalent_and_independently_owned(container):
    first = kickoff_fixture()
    before = semantic(first)
    rng = first.capture_rng_state()
    second = kickoff_fixture(forward=False)
    assert semantic(first) == before, "constructing another fixture changed the first rules"
    assert semantic(second) == before
    independent(first, second)

    play(second, ball_decision(second))
    play(second, next(spec for spec in choices(second) if spec[0] == "START_MOVE"))
    independent(first, second)
    assert semantic(first) == before and first.capture_rng_state() == rng
    second_before = semantic(second)
    getattr(second.ruleset, container).clear()
    assert semantic(second) != second_before  # Rules are observable, not excluded.
    assert semantic(first) == before and first.capture_rng_state() == rng

    third = kickoff_fixture(forward=False)
    assert semantic(third) == before  # Mutating a game cannot poison the blueprint.
    independent(first, second, third)


def test_independence_observer_detects_nested_rule_alias():
    first = kickoff_fixture()
    second = kickoff_fixture(forward=False)
    before = semantic(first)
    independent(first, second)
    # Distinct outer containers are insufficient: inject an equivalent nested alias.
    second.ruleset.races[0].roles = first.ruleset.races[0].roles
    assert semantic(first) == semantic(second) == before
    with pytest.raises(AssertionError, match="shared mutable objects"):
        independent(first, second)


def test_observer_dictionary_order_preserves_mixed_keys_and_normalized_ties():
    game = kickoff_fixture(size=1)
    square = game.get_square(1, 2)
    # A Square and this tuple are distinct Python keys with the same observation.
    # Insertion order must not decide the order of their differently valued entries.
    entries = [(1, "integer"), (10, "prefix"), (1.5, "float"), ("a", "text"),
               ("a'", "quote"), (None, "none"), (square, "z"), (("square", 1, 2), "a")]
    game.state.observer_probe = dict(entries)
    before = semantic(game)
    observed = dict(before["state"])["observer_probe"]
    encoded_keys = [1, 10, 1.5, "a", "a'", None, ["square", 1, 2], ["square", 1, 2]]
    assert observed == sorted([[key, value] for key, (_, value) in zip(encoded_keys, entries)], key=repr)
    game.state.observer_probe = dict(reversed(entries))
    assert semantic(game) == before
    game.state.observer_probe[square] = "changed"
    assert semantic(game) != before


def test_observer_refreshes_public_fields_and_nested_values_between_observations():
    class Parent:
        inherited = ["original"]

    class Probe(Parent):
        static_value = staticmethod([0])

        def method(self):
            pass

        @property
        def property_value(self):
            return self.nested["value"]

    game = kickoff_fixture(size=1)
    probe = Probe()
    probe.nested = {"value": [1]}
    probe.method = "instance override"
    game.state.observer_probe = probe
    before = semantic(game)
    probe.nested["value"].append(2)
    assert semantic(game) != before
    before = semantic(game)
    Parent.inherited.append("changed")
    assert semantic(game) != before
    before = semantic(game)
    Probe.added_default = [3]
    assert semantic(game) != before
    before = semantic(game)
    probe.new_instance_field = [4]
    assert semantic(game) != before
    del probe.new_instance_field
    assert semantic(game) == before
    # Metadata acceleration must match the general observer's public lookup,
    # including an instance field shadowing a method and a computed property.
    assert fields(probe, {}) == fields(probe)

    class CustomLookup(Probe):
        def __dir__(self):
            return ["virtual"]

        def __getattribute__(self, name):
            return [5] if name == "virtual" else super().__getattribute__(name)

    custom = CustomLookup()
    assert fields(custom, {}) == fields(custom) == {"virtual": [5]}
