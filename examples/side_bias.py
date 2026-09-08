"""CPU-only paired side investigation (issue #19), not a balance benchmark.

Run from the checkout: python -m examples.side_bias --help
The engine is never reflected or patched. Only the mirrored policy's private
decision view is reflected; its selected action is mapped back and validated.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
import hashlib
import io
import json
import math
from pathlib import Path
import pickle
import platform
import subprocess

import numpy as np
import botbowl as bb
import botbowl.core.pathfinding as pf
from examples.scripted_bot_example import MyScriptedBot


POLICIES = ("random-legal", "scripted", "scripted-mirrored")


def dumps(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def reflect_square(square, width):
    return None if square is None else bb.Square(width - 1 - square.x, square.y,
                                                square._out_of_bounds)


def reflect_action(action, game):
    return bb.Action(action.action_type,
                     player=game.get_player(action.player.player_id) if action.player else None,
                     position=reflect_square(action.position, game.arena.width))


def policy_view(game, reflected):
    """Copy all geometry, including procedure fields and cached paths.

    Pickle persistent IDs map *every* Square, including non-board squares and
    dictionary keys, without mutating hash keys. Only our own in-memory game is
    deserialized. Reports and agents are omitted: the example policy does not
    read reports, and retaining agents would recursively copy policy queues.
    This is a decision view, never a simulation or a coupled dice experiment.
    """
    buffer = io.BytesIO()
    squares, agents = {}, {}

    class Writer(pickle.Pickler):
        def persistent_id(self, obj):
            if isinstance(obj, bb.Square):
                return ("square", obj.x, obj.y, obj._out_of_bounds)
            if obj is game.state.reports:
                return ("reports",)
            if isinstance(obj, bb.Agent):
                return ("agent", obj.agent_id, obj.name)
            return None

    class Reader(pickle.Unpickler):
        def persistent_load(self, token):
            if token[0] == "square":
                if token not in squares:
                    _, x, y, out = token
                    squares[token] = bb.Square(game.arena.width - 1 - x if reflected else x, y, out)
                return squares[token]
            if token[0] == "agent":
                if token not in agents:
                    agents[token] = bb.Agent(token[2], agent_id=token[1], human=True)
                return agents[token]
            if token == ("reports",):
                return []
            raise ValueError(token)

    Writer(buffer, protocol=pickle.HIGHEST_PROTOCOL).dump(game)
    buffer.seek(0)
    view = Reader(buffer).load()
    if reflected:
        view.state.home_team, view.state.away_team = view.state.away_team, view.state.home_team
        view.home_agent, view.away_agent = view.away_agent, view.home_agent
        view.state.teams = [view.state.home_team, view.state.away_team]
        view.state.pitch.board = [list(reversed(row)) for row in view.state.pitch.board]
        view.state.pitch.squares = [list(reversed(row)) for row in view.state.pitch.squares]
        view.square_shortcut = view.state.pitch.squares
    # Choice ordering is a declared policy intervention. Keep aligned metadata
    # aligned; helper/path searches now run in the same canonical coordinates.
    for choice in view.state.available_actions:
        if choice.positions:
            target = "positions"
            keys = [(s.x, s.y) if s is not None else (-1, -1) for s in choice.positions]
        else:
            target = "players"
            keys = [(p.position.x, p.position.y, p.nr) if p.position else (-1, -1, p.nr)
                    for p in choice.players]
        order = sorted(range(len(keys)), key=keys.__getitem__)
        for field in (target, "rolls", "block_dice", "paths"):
            values = getattr(choice, field)
            if values and len(values) == len(order):
                object.__setattr__(choice, field, [values[i] for i in order])
    return view


class Scripted(MyScriptedBot):
    def end_game(self, game):
        pass  # Keep stdout machine-readable; outcomes belong in episode records.


class MirroredScripted(bb.Agent):
    def __init__(self, name):
        super().__init__(name)
        self.policy = Scripted(name)
        self.policy.agent_id = self.agent_id

    def new_game(self, game, team):
        self.team_id = team.team_id

    def act(self, game):
        reflected = self.team_id == game.state.away_team.team_id
        view = policy_view(game, reflected)
        self.policy.my_team = view.get_team_by_id(self.team_id)
        self.policy.opp_team = view.get_opp_team(self.policy.my_team)
        # Identity-based lookups inside the example also see this policy.
        view.home_agent = self.policy
        action = self.policy.act(view)
        if reflected:
            return reflect_action(action, game)
        return bb.Action(action.action_type, position=action.position,
                         player=game.get_player(action.player.player_id) if action.player else None)

    def end_game(self, game):
        pass


class RandomLegal(bb.Agent):
    """Uniform choice family then uniform valid target; not uniform over moves.

    Setup uses a built-in formation then ends as soon as legal. This avoids the
    stock random bot's repeated END_SETUP rejections and unbounded setup loop.
    """
    def __init__(self, name, seed):
        super().__init__(name)
        self.rnd = np.random.RandomState(seed)

    def new_game(self, game, team):
        pass

    def act(self, game):
        choices = [c for c in game.get_available_actions() if not c.disabled]
        if isinstance(game.get_procedure(), bb.Setup):
            if game.is_setup_legal(game.active_team):
                return bb.Action(bb.ActionType.END_SETUP)
            choices = [c for c in choices if c.action_type.name.startswith("SETUP_FORMATION_")]
        # PLACE_PLAYER is handled by formation macros above. All other offered
        # target combinations are checked at the public validation boundary.
        families = []
        for choice in choices:
            candidates = [bb.Action(choice.action_type, player=p, position=s)
                          for p in (choice.players or [None])
                          for s in (choice.positions or [None])]
            legal = [a for a in candidates if game.is_action_allowed(a)]
            if legal:
                families.append(legal)
        if not families:
            raise RuntimeError("No legal action family")
        family = families[self.rnd.randint(len(families))]
        return family[self.rnd.randint(len(family))]

    def end_game(self, game):
        pass


def policy_seed(seed, identity):
    return int.from_bytes(hashlib.sha256(f"issue19:{seed}:{identity}".encode()).digest()[:4], "big")


@dataclass(frozen=True)
class Episode:
    pair: int
    leg: int
    seed: int
    policy_a: str = "scripted"
    policy_b: str = "scripted"
    pathfinding: bool = False
    receiver: str = "A"
    size: int = 11
    rounds: int = 8
    kickoff: bool = True
    max_decisions: int = 20000

    @property
    def home(self):
        return "A" if self.leg == 0 else "B"


def paired_episodes(pairs, seed=19000, **kwargs):
    for pair in range(pairs):
        for leg in (0, 1):
            yield Episode(pair, leg, seed + pair, receiver="AB"[pair % 2], **kwargs)


def make_game(spec):
    """Reload both rosters, rules, config and agents on *every* episode."""
    config = bb.load_config("bot-bowl" if spec.size == 11 else f"gym-{spec.size}")
    config.competition_mode = False
    config.debug_mode = False
    config.pathfinding_enabled = spec.pathfinding
    config.rounds = spec.rounds
    config.kick_off_table = spec.kickoff
    rules = bb.load_rule_set(config.ruleset, all_rules=True)
    identities = (spec.home, "B" if spec.home == "A" else "A")
    teams, agents = [], []
    for identity in identities:
        team = bb.load_team_by_filename("human", rules, board_size=spec.size)
        team.team_id = identity
        for player in team.players:
            player.player_id = f"{identity}-{player.nr}"
        teams.append(team)
        policy = spec.policy_a if identity == "A" else spec.policy_b
        if policy == "random-legal":
            agent = RandomLegal(identity, policy_seed(spec.seed, identity))
        elif policy == "scripted":
            agent = Scripted(identity)
        elif policy == "scripted-mirrored":
            agent = MirroredScripted(identity)
        else:
            raise ValueError(policy)
        agent.agent_id = identity
        if isinstance(agent, MirroredScripted):
            agent.policy.agent_id = identity
        agents.append(agent)
    return bb.Game(f"pair-{spec.pair}-{spec.leg}", *teams, *agents, config,
                   ruleset=rules, seed=spec.seed, external_control=True)


def action_record(action):
    if action is None:
        return None
    return [action.action_type.name, action.player.player_id if action.player else None,
            [action.position.x, action.position.y] if action.position else None]


def config_record(config):
    result = {k: v for k, v in vars(config).items()
              if isinstance(v, (str, int, float, bool)) or v is None}
    result["time_limits"] = vars(config.time_limits).copy()
    for name in ("offensive_formations", "defensive_formations"):
        result[name] = [{"name": f.name, "rows": ["".join(row) for row in f.formation]}
                        for f in getattr(config, name)]
    return result


def run_episode(spec, trace_path=None):
    game = make_game(spec)
    game.init()
    trace = []
    cause, error = "decision_limit", None
    decisions = 0
    actions = Counter()
    kickoffs = []
    for decisions in range(1, spec.max_decisions + 1):
        try:
            if game.state.game_over:
                decisions -= 1
                cause = "completed"
                break
            if isinstance(game.get_procedure(), bb.CoinTossKickReceive):
                # Retain random toss, but experimentally balance first receiver
                # by identity and keep that identity when swapping physical sides.
                receive = game.active_team.team_id == spec.receiver
                action = bb.Action(bb.ActionType.RECEIVE if receive else bb.ActionType.KICK)
            elif any(c.action_type == bb.ActionType.START_GAME for c in game.get_available_actions()):
                action = bb.Action(bb.ActionType.START_GAME)
            else:
                action = game.actor.act(game) if game.actor else None
            entry = dict(decision=decisions, actor=game.actor.name if game.actor else None,
                         procedure=type(game.get_procedure()).__name__, action=action_record(action))
            trace.append(entry)
            if action:
                actions[action.action_type.name] += 1
            result = game.advance(action)
            entry["events"] = [event.to_json() for event in result.events]
            for event in result.events:
                if event.outcome_type.name.startswith("KICKOFF_"):
                    receiving = game.get_receiving_team()
                    kickoffs.append(dict(event=event.outcome_type.name, half=game.state.half,
                                         receiver=receiving.team_id,
                                         side="home" if receiving is game.state.home_team else "away"))
        except Exception as exc:
            cause = "invalid_action" if isinstance(exc, bb.InvalidActionError) else "exception"
            error = {"type": type(exc).__name__, "message": str(exc),
                     "procedure": type(game.get_procedure()).__name__}
            break
    if game.state.game_over:
        cause = "completed"
    home_score, away_score = (t.state.score for t in game.state.teams)
    receiver = game.state.receiving_first_half
    record = dict(spec=asdict(spec), home=spec.home, away="B" if spec.home == "A" else "A",
                  policy_seeds={i: policy_seed(spec.seed, i) for i in "AB"},
                  config=config_record(game.config),
                  roster="human", all_rules=True, backend=pf.get_safest_path.__module__,
                  first_receiver=receiver.team_id if receiver else None,
                  first_receiver_side=("home" if receiver is game.state.home_team else "away") if receiver else None,
                  score=[home_score, away_score], end_cause=cause, error=error,
                  kickoffs=kickoffs,
                  decisions=decisions, actions=dict(sorted(actions.items())),
                  outcome=("home" if home_score > away_score else "away" if away_score > home_score else "draw")
                  if cause == "completed" else None,
                  events=dict(sorted(Counter(r.outcome_type.name for r in game.state.reports).items())))
    # Stable roster IDs and semantic events exclude wall clocks and UUIDs.
    record["trace_sha256"] = hashlib.sha256(dumps(trace).encode()).hexdigest()
    if trace_path:
        Path(trace_path).write_text(dumps(trace))
    return record


def summarize(records):
    """Treat pairs as clusters, including in policy/kickoff strata.

    Conservative 95% Hoeffding bounds apply to equal-weight independent pair
    means in [0,1], NOT to independent episodes or event-matched random rolls.
    Seeds are pseudorandom samples; this assumption is explicit in the report.
    Incomplete pairs are excluded from estimands, retained in failure counts.
    """
    groups = defaultdict(list)
    for row in records:
        spec = row["spec"]
        key = (spec["policy_a"], spec["policy_b"], spec["pathfinding"], row["backend"],
               spec["size"], spec["rounds"], spec["kickoff"])
        groups[key].append(row)
    output = []
    for key, rows in sorted(groups.items()):
        pairs = defaultdict(list)
        for row in rows:
            pairs[row["spec"]["pair"]].append(row)
        complete = []
        for pair in pairs.values():
            if len({r["spec"]["leg"] for r in pair}) != len(pair):
                raise ValueError("Duplicate pair leg")
            if len(pair) == 2 and {r["spec"]["leg"] for r in pair} == {0, 1} and all(
                    r["end_cause"] == "completed" for r in pair):
                left, right = sorted(pair, key=lambda r: r["spec"]["leg"])
                if replace(Episode(**left["spec"]), leg=1) != Episode(**right["spec"]):
                    raise ValueError("Paired specifications differ beyond side swap")
                complete.extend(pair)
        strata = {}
        for label in ("all", "receive-home", "receive-away"):
            selected = [r for r in complete if label == "all" or
                        label == "receive-" + r["first_receiver_side"]]
            clusters = defaultdict(list)
            counts = Counter(r["outcome"] for r in selected)
            for row in selected:
                clusters[row["spec"]["pair"]].append({"home": 1, "away": 0, "draw": 0.5}[row["outcome"]])
            means = [sum(values) / len(values) for values in clusters.values()]
            estimate = sum(means) / len(means) if means else None
            radius = math.sqrt(math.log(40) / (2 * len(means))) if means else None
            strata[label] = dict(episodes=len(selected), pairs=len(means),
                                 home_wins=counts["home"], away_wins=counts["away"], draws=counts["draw"],
                                 home_points=estimate,
                                 hoeffding95=[max(0, estimate - radius), min(1, estimate + radius)] if means else None)
        output.append(dict(policy_a=key[0], policy_b=key[1], pathfinding=key[2], backend=key[3],
                           size=key[4], rounds=key[5], kickoff=key[6], attempted=len(rows),
                           end_causes=dict(sorted(Counter(r["end_cause"] for r in rows).items())),
                           excluded_episodes=len(rows) - len(complete), strata=strata))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=19000)
    parser.add_argument("--policy", choices=POLICIES, default="scripted")
    parser.add_argument("--opponent", choices=POLICIES)
    parser.add_argument("--pathfinding", choices=("on", "off"), default="off")
    parser.add_argument("--size", type=int, choices=(1, 3, 5, 7, 11), default=11)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--max-decisions", type=int, default=20000)
    parser.add_argument("--no-kickoff", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.pairs < 1 or args.rounds < 1 or args.max_decisions < 1 or not 0 <= args.seed < 2**32 - args.pairs:
        parser.error("Positive bounds and valid uint32 seeds required")
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = dict(python=platform.python_version(), numpy=np.__version__,
                    revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (Path("examples/side_bias.py"), Path("examples/scripted_bot_example.py"))},
                    backend=pf.get_safest_path.__module__)
    (args.output / "environment.json").write_text(dumps(metadata))
    records = []
    with (args.output / "episodes.jsonl").open("w") as stream:
        for spec in paired_episodes(args.pairs, args.seed, policy_a=args.policy,
                                    policy_b=args.opponent or args.policy,
                                    pathfinding=args.pathfinding == "on", size=args.size,
                                    rounds=args.rounds, kickoff=not args.no_kickoff,
                                    max_decisions=args.max_decisions):
            row = run_episode(spec, args.output / f"trace-{spec.pair}-{spec.leg}.json")
            records.append(row)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            print(f"pair={spec.pair} leg={spec.leg} score={row['score']} end={row['end_cause']}", flush=True)
    (args.output / "summary.json").write_text(dumps(summarize(records)))
    if any(r["end_cause"] != "completed" for r in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
