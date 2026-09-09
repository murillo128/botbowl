"""Atomic data patches at regular team-turn decisions (SIM-08).

No action, RNG operation, procedure constructor or input-selected Python path is
part of this editor. Continuation chance is selected later by BranchTree.fork.
"""
from dataclasses import dataclass

from botbowl.core import procedure
from .actions import ActionControl, ActionV1
from .branches import BranchSnapshot
from .records import MAX_RECORD_BYTES, decode_json, encode_json, identifier, keys
from .rules import describe_rules, intervention_configuration
from .snapshot_io import snapshot_hash, write_snapshot
from .snapshots import capture_snapshot, clone_from_snapshot


EDITOR_VERSION = '1.0.0'
ATTRIBUTES = ('extra_ma', 'extra_st', 'extra_ag', 'extra_av')
RESOURCES = ('rerolls', 'bribes', 'babes', 'apothecaries')


class InterventionError(ValueError):
    """A patch, witness or decision boundary is outside the editor contract."""


def _check(condition, message):
    if not condition:
        raise InterventionError(message)


def _copy(value):
    return decode_json(encode_json(value))


def _equal(left, right):
    # Python considers True == 1; old-value guards must not.
    return encode_json(left) == encode_json(right)


def _position(square):
    return None if square is None else {'x': square.x, 'y': square.y}


def _square(game, value):
    keys(value, ('x', 'y'))
    _check(all(type(value[key]) is int for key in ('x', 'y')), 'Position requires integers')
    _check(1 <= value['x'] < game.arena.width - 1 and
           1 <= value['y'] < game.arena.height - 1, 'Position outside playable board')
    return game.get_square(value['x'], value['y'])


def _ball(game):
    _check(len(game.state.pitch.balls) == 1, 'Exactly one settled ball is supported')
    ball = game.state.pitch.balls[0]
    carrier = game.get_player_at(ball.position) if ball.is_carried and ball.position else None
    return {'position': _position(ball.position),
            'carrier_id': None if carrier is None else carrier.player_id}


def _rules(game):
    return describe_rules(game.config, game.ruleset, game.arena,
                          game.state.home_team, game.state.away_team).to_json()


def _boundary(game):
    _check(not game.state.game_over and type(game.get_procedure()) is procedure.Turn and
           not game.get_procedure().blitz and not game.get_procedure().quick_snap and
           game.state.active_player is None,
           'Pending decision incompatible: edit only between activations in a regular Turn')
    # A regular Turn must not hide an unresolved player/kickoff procedure below it.
    _check(all(type(proc) in (procedure.EndGame, procedure.Pregame, procedure.Half,
                             procedure.Turn) for proc in game.state.stack.items),
           'Pending procedure context cannot be rebuilt unambiguously')
    _check(game.timeline is not None, 'Interventions require a logical timeline')


def _consistent(game):
    occupied = set()
    for team in game.state.teams:
        dugout = game.state.dugouts[team.team_id]
        groups = (dugout.reserves, dugout.kod, dugout.casualties, dugout.dungeon)
        for player in team.players:
            _check(player.team is team, 'Player/team membership mismatch')
            count = sum(sum(item is player for item in group) for group in groups)
            if player.position is None:
                _check(count == 1, 'Off-pitch player must belong to exactly one dugout group')
            else:
                square = _square(game, _position(player.position))
                key = (square.x, square.y)
                _check(key not in occupied, 'Player position collision')
                occupied.add(key)
                _check(count == 0 and game.get_player_at(square) is player,
                       'Pitch/dugout membership mismatch')
                _check(not player.state.in_air and not player.state.picked_up,
                       'Airborne player context is unsupported')
        for field in RESOURCES:
            value = getattr(team.state, field)
            _check(type(value) is int and 0 <= value < 2**31, 'Invalid resource domain')
    _check(game.state.pitch.bomb is None, 'Unsettled bomb context is unsupported')
    data = _ball(game)
    ball = game.state.pitch.balls[0]
    _square(game, data['position'])
    _check(ball.on_ground, 'Airborne ball context is unsupported')
    if ball.is_carried:
        _check(data['carrier_id'] is not None, 'Carried ball has no carrier')
        carrier = game.get_player(data['carrier_id'])
        _check(carrier.state.up and not carrier.state.stunned, 'Carrier must be standing')


def _spec(value):
    data = _copy(value)
    _check(type(data) is dict, 'Expected an intervention object')
    data.setdefault('reachability', 'synthetic')
    data.setdefault('recipe', None)
    keys(data, ('schema_version', 'branch_id', 'snapshot_id', 'reason', 'author',
                'operations', 'reachability', 'recipe'))
    _check(type(data['schema_version']) is int and data['schema_version'] == 1,
           'Unsupported intervention schema version')
    for field in ('branch_id', 'snapshot_id'):
        identifier(data[field])
    for field in ('reason', 'author'):
        _check(type(data[field]) is str and 0 < len(data[field].strip()) <= 4096,
               'Declare reason and author')
    _check(type(data['operations']) is list and 1 <= len(data['operations']) <= 256,
           'Expected 1..256 operations')
    _check(data['reachability'] in ('synthetic', 'unknown', 'validated_recipe'),
           'Unknown reachability classification')
    recipe = data['recipe']
    if data['reachability'] == 'validated_recipe':
        keys(recipe, ('actions',))
        _check(type(recipe['actions']) is list and 1 <= len(recipe['actions']) <= 256,
               'A recipe requires 1..256 replayed legal actions')
        for action in recipe['actions']:
            ActionV1.from_json(action)
    else:
        _check(recipe is None, 'Recipe requires validated_recipe classification')
    return data


@dataclass(frozen=True)
class InterventionResult:
    snapshot: BranchSnapshot
    _provenance: bytes

    @property
    def provenance(self):
        """Fresh inert data; declared authorship is not authentication."""
        return decode_json(self._provenance)

    def write(self, path):
        """Persist executable state and complete provenance in one atomic file."""
        return write_snapshot(path, self.snapshot.engine, provenance=self.provenance)


def apply_intervention(snapshot, intervention_spec, *, recipe_start=None):
    """Clone a BranchSnapshot, validate a whole patch, and return an intervened branch.

    Old values all refer to the parent. Positions support simultaneous swaps of
    on-pitch players; ball coordinates follow their existing carrier unless a
    ball operation explicitly overrides possession. No legal decision is taken.

    ``recipe_start`` is a trusted BranchSnapshot in the same origin family. Only
    a nonempty legal replay whose *complete semantic hash* equals the edited
    state can attest reachability; public-board similarity is insufficient.
    """
    game = None
    try:
        data = _spec(intervention_spec)
        _check(type(snapshot) is BranchSnapshot and snapshot.engine.scope == 'engine',
               'Expected a BranchSnapshot with engine scope')
        for value in (snapshot.branch_id, snapshot.snapshot_id, snapshot.origin_family_id):
            identifier(value)
        _check(data['branch_id'] != snapshot.branch_id and data['snapshot_id'] != snapshot.snapshot_id,
               'Intervention must have new branch and snapshot IDs')
        game = clone_from_snapshot(snapshot.engine)
        _check(game.timeline is not None and game.timeline.context.branch_id == snapshot.branch_id,
               'Snapshot branch mismatch')
        _boundary(game)
        _consistent(game)
        pre_hash = snapshot_hash(snapshot.engine)
        prior_rules = _rules(game)
        parent = game.timeline.context.to_json()
        original_ball = _ball(game)
        changes, seen = [], set()
        for operation in data['operations']:
            keys(operation, ('entity', 'entity_id', 'field', 'old_value', 'new_value'))
            kind, entity_id, field = (operation[key] for key in ('entity', 'entity_id', 'field'))
            _check(all(type(value) is str for value in (kind, entity_id, field)),
                   'Entity, entity_id and field must be strings')
            target = (kind, entity_id, field)
            _check(target not in seen, 'Duplicate operation target')
            seen.add(target)
            value = operation['new_value']
            if kind == 'player':
                _check(entity_id in game.state.player_by_id, 'Unknown player ID')
                obj = game.get_player(entity_id)
                if field == 'position':
                    _check(obj.position is not None, 'Position patches require an on-pitch player')
                    old, value = _position(obj.position), _square(game, value)
                else:
                    _check(field in ATTRIBUTES, 'Forbidden player attribute')
                    _check(type(value) is int and -9 <= value <= 9 and
                           1 <= getattr(obj.role, field[6:]) + value <= 10,
                           'Invalid public attribute domain')
                    old = getattr(obj, field)
            elif kind == 'team':
                _check(entity_id in game.state.team_by_id, 'Unknown team ID')
                _check(field in RESOURCES, 'Forbidden team resource')
                _check(type(value) is int and 0 <= value < 2**31, 'Invalid resource domain')
                obj = game.state.team_by_id[entity_id].state
                old = getattr(obj, field)
            elif kind == 'ball':
                _check(entity_id == 'ball' and field == 'placement', 'Unknown ball field or ID')
                keys(value, ('position', 'carrier_id'))
                _square(game, value['position'])
                _check(value['carrier_id'] is None or type(value['carrier_id']) is str and
                       value['carrier_id'] in game.state.player_by_id, 'Unknown ball carrier')
                obj, old = game.state.pitch.balls[0], original_ball
            elif kind == 'configuration':
                _check(entity_id == 'rules' and field == 'registered_config', 'Forbidden rules field')
                keys(value, ('config_id', 'version'))
                obj, old = game, prior_rules['config_digest']
                value = intervention_configuration(game.config, **value)
            else:
                raise InterventionError('Forbidden entity')
            _check(_equal(old, operation['old_value']), 'old_value mismatch')
            changes.append((kind, obj, field, value))
        # All writes are on a private clone. Remove first to allow atomic swaps.
        for kind, obj, field, value in changes:
            if kind == 'player' and field == 'position':
                game.remove(obj)
        for kind, obj, field, value in changes:
            if kind == 'player' and field == 'position':
                _check(game.get_player_at(value) is None, 'Player position collision')
                game.put(obj, value)
            elif kind in ('player', 'team'):
                setattr(obj, field, value)
            elif kind == 'configuration':
                game.config = value
        if original_ball['carrier_id'] is not None:
            game.state.pitch.balls[0].move_to(game.get_player(original_ball['carrier_id']).position)
        for kind, obj, field, value in changes:
            if kind == 'ball':
                square = _square(game, value['position'])
                if value['carrier_id'] is not None:
                    _check(game.get_player(value['carrier_id']).position == square,
                           'Ball/carrier position mismatch')
                obj.move_to(square)
                obj.is_carried = value['carrier_id'] is not None
                obj.on_ground = True  # Engine uses on_ground=True for settled carried balls too.
        _consistent(game)
        game.set_available_actions()
        _check(bool(game.state.available_actions), 'Editor cannot invent a no-op decision')
        # Capturing/recloning discards editor undo steps and rebuilds derived caches.
        edited = capture_snapshot(game)
        edited_hash = snapshot_hash(edited)
        witness = None
        if data['reachability'] == 'validated_recipe':
            _check(type(recipe_start) is BranchSnapshot and
                   recipe_start.origin_family_id == snapshot.origin_family_id,
                   'Recipe requires a start snapshot in the same origin family')
            witness_game = clone_from_snapshot(recipe_start.engine)
            try:
                _check(witness_game.timeline.context.episode_id == parent['episode_id'],
                       'Recipe episode mismatch')
                for action in data['recipe']['actions']:
                    control = ActionControl(witness_game)
                    core = control.decode(control.request(ActionV1.from_json(action)))
                    witness_game.advance(core, max_steps=100000)
                _check(snapshot_hash(capture_snapshot(witness_game)) == edited_hash,
                       'Replayed recipe does not reach the complete edited state')
                witness = {'parent_snapshot_id': recipe_start.snapshot_id,
                           'start_hash': snapshot_hash(recipe_start.engine),
                           'end_hash': edited_hash, 'actions': data['recipe']['actions']}
            finally:
                witness_game.close()
        else:
            _check(recipe_start is None, 'Unused recipe start snapshot')
        branched = clone_from_snapshot(edited, branch_id=data['branch_id'])
        try:
            engine = capture_snapshot(branched)
            post_hash = snapshot_hash(engine)
        finally:
            branched.close()
        provenance = dict(
            schema_version=1, kind='intervened', origin_family_id=snapshot.origin_family_id,
            branch_id=data['branch_id'], snapshot_id=data['snapshot_id'],
            parent_branch_id=snapshot.branch_id,
            parent_snapshot_id=snapshot.snapshot_id, parent=parent,
            patch=data, editor_version=EDITOR_VERSION, pre_hash=pre_hash, post_hash=post_hash,
            edited_hash=edited_hash,
            rules_before=prior_rules, rules_effective=_rules(game),
            reachability=data['reachability'], recipe_witness=witness,
            continuation_chance=None,
        )
        return InterventionResult(BranchSnapshot(data['snapshot_id'], data['branch_id'],
                                                 snapshot.origin_family_id, engine),
                                  encode_json(provenance))
    except (ValueError, KeyError, TypeError) as error:
        if isinstance(error, InterventionError):
            raise
        raise InterventionError(str(error)) from error
    finally:
        if game is not None:
            game.close()


def main(argv=None):
    import argparse
    from pathlib import Path
    from .snapshot_io import read_snapshot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot')
    parser.add_argument('patch')
    parser.add_argument('output')
    parser.add_argument('--recipe-start')
    args = parser.parse_args(argv)

    def bounded_read(path, maximum):
        with open(path, 'rb') as stream:
            raw = stream.read(maximum + 1)
        _check(len(raw) <= maximum, 'Input file exceeds byte limit')
        return raw

    def load(path):
        import json
        # Family/identity come from the source artifact, never a CLI rename.
        engine = read_snapshot(path)
        raw = bounded_read(path, 32 * 1024 * 1024)
        document = json.loads(raw)
        _check(document['semantic_state_hash'] == snapshot_hash(engine),
               'Source changed while reading provenance')
        provenance = document['provenance']
        return BranchSnapshot(provenance['snapshot_id'], provenance['branch_id'],
                              provenance['origin_family_id'], engine)

    try:
        _check(all(Path(args.output).resolve() != Path(path).resolve()
                   for path in (args.snapshot, args.patch, args.recipe_start) if path is not None),
               'Output must differ from source inputs')
        result = apply_intervention(load(args.snapshot), decode_json(bounded_read(args.patch, MAX_RECORD_BYTES)),
                                    recipe_start=None if args.recipe_start is None else load(args.recipe_start))
        result.write(args.output)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, 'intervention rejected: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
