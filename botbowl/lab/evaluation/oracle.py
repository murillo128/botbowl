"""Opt-in, detached evaluation labels; never a policy input or a plugin loader."""
from dataclasses import asdict, dataclass, field
import re
from types import MappingProxyType
from typing import Any, Dict, Literal, Optional, Sequence, Tuple, Union

from botbowl.core.game import Game
from botbowl.lab.observations import ObservationControl, observe
from botbowl.lab.records import (
    _Record, context as validate_time, decode_json, encode_json, identifier,
    integer, keys, require, RecordError, validate_rules_descriptor,
)
from botbowl.lab.rules import RulesDescriptor
from botbowl.lab.timeline import TimelineContext


Category = Literal['state_fact', 'exact_rule_quantity', 'estimated', 'heuristic', 'human_annotation']
UnavailableCode = Literal['unknown_entity', 'invalid_pair', 'off_pitch', 'terminal',
                          'unsupported_state', 'external_required']
LabelValue = Union[None, bool, int, float, str, Dict[str, int]]


@dataclass(frozen=True)
class Unavailability:
    code: UnavailableCode
    reason: str


class UnknownLabelError(RecordError):
    """The requested label is not in this code-owned catalogue."""


@dataclass(frozen=True)
class LabelSpec:
    name: str
    version: int
    category: Category
    value_type: str
    unit: str
    domain: str
    origin: str
    logical_availability: str
    visibility: str
    observation_path: Optional[str]
    conditions: str

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


_BLOCK_CONDITIONS = (
    'BB2016 shipped tables; nonterminal; standing, grounded, unstunned, unrooted adjacent opposing registered players; '
    'only Block/Dodge/Tackle/Strip Ball/Sure Hands skills on pitch; '
    'all defender neighbours except attacker empty and playable; zero or one coherent ball. '
    'Conditional on one ordinary block roll being reached, issue8_local_v1 face selection, '
    'no reroll. No activation, blitz, armour/injury, follow-up or ball continuation. '
    'Four overlapping direct-effect marginals, not a partition or tactical value.'
)
_BLOCK_FIELDS = ('attacker_down', 'defender_down', 'attacker_ball_loss', 'defender_ball_loss')


def _catalogue() -> Dict[str, LabelSpec]:
    specs = []
    for name, kind, unit, domain, path, condition in (
        ('match.terminal', 'boolean', 'boolean', 'match', 'match.game_over', 'Current stored terminal flag.'),
        ('team.score', 'integer', 'touchdown', 'team', 'teams[].score', 'Current stored score.'),
        ('team.possession', 'boolean', 'boolean', 'team', 'balls[].carrier',
         'Exactly one coherent ball; true iff its current carrier belongs to this side. Loose ball means false.'),
        ('team.rerolls', 'integer', 'reroll', 'team', 'teams[].resources.rerolls', 'Remaining stored resource; not eligibility.'),
        ('team.apothecaries', 'integer', 'apothecary', 'team', 'teams[].resources.apothecaries', 'Remaining stored resource.'),
        ('team.bribes', 'integer', 'bribe', 'team', 'teams[].resources.bribes', 'Remaining stored resource.'),
        ('team.reroll_used', 'boolean', 'boolean', 'team', 'teams[].resources.reroll_used', 'Stored turn flag.'),
        ('team.wizard_available', 'boolean', 'boolean', 'team', 'teams[].resources.wizard_available', 'Stored resource flag.'),
        ('player.position', 'position', 'arena_cell', 'player', 'players[].position', 'On playable pitch; absolute x,y, upper-left origin.'),
        ('player.location', 'location', 'compartment', 'player', 'players[].location', 'Pitch/reserves/ko/casualties/dungeon/unplaced, including terminal.'),
        ('player.up', 'boolean', 'boolean', 'player', 'players[].status.up', 'Stored flag, also off pitch and terminal; not action eligibility.'),
        ('player.stunned', 'boolean', 'boolean', 'player', 'players[].status.stunned', 'Stored flag, also off pitch and terminal.'),
        ('player.used', 'boolean', 'boolean', 'player', 'players[].status.used', 'Stored flag, also off pitch and terminal.'),
        ('ball.position', 'position', 'arena_cell', 'ball', 'balls[].position', 'On playable pitch; absolute x,y, upper-left origin.'),
        ('ball.carrier', 'nullable_player_id', 'entity_id', 'ball', 'balls[].carrier', 'Exactly one coherent ball; null means loose, not unavailable.'),
        ('geometry.manhattan', 'integer', 'arena_cell', 'entity_pair', None, '|dx| + |dy|; both entities on playable pitch; no obstacles/path cost.'),
        ('geometry.chebyshev', 'integer', 'arena_cell', 'entity_pair', None, 'max(|dx|, |dy|); both entities on playable pitch; no reachability claim.'),
        ('geometry.adjacent', 'boolean', 'boolean', 'entity_pair', None, 'Chebyshev distance exactly 1; not same cell; no tackle-zone claim.'),
    ):
        specs.append(LabelSpec(name, 1, 'state_fact', kind, unit, domain,
                               'ObservationV1' if path else 'ObservationV1 coordinates / integer arithmetic',
                               'captured_boundary', 'evaluation_only', path, condition))
    for field in _BLOCK_FIELDS:
        specs.append(LabelSpec('block.' + field, 1, 'exact_rule_quantity', 'probability',
                               'probability', 'opposing_player_pair',
                               'Game.get_block_outcome_probs', 'captured_boundary',
                               'evaluation_only', None, _BLOCK_CONDITIONS))
    for name, category, kind, unit, domain, condition in (
        ('estimated.win_probability', 'estimated', 'probability', 'probability', 'team',
         'Externally estimated probability of strictly higher terminal score for this side; draws are not wins. Horizon must be terminal.'),
        ('heuristic.positional_advantage', 'heuristic', 'signed_score', 'heuristic_score', 'team',
         'External score in [-1,1]; sign/scale belong to the named method, not exact tactical quality.'),
        ('human.tactical_annotation', 'human_annotation', 'text', 'annotation', 'player',
         'External human interpretation, not a simulator fact or ground truth.'),
    ):
        specs.append(LabelSpec(name, 1, category, kind, unit, domain, 'external',
                               'declared_available_at', 'evaluation_only', None, condition))
    return {spec.name: spec for spec in specs}


LABEL_SPECS = MappingProxyType(_catalogue())


def label_spec(name: str) -> LabelSpec:
    if type(name) is not str or name not in LABEL_SPECS:
        raise UnknownLabelError('Unknown evaluation label: ' + str(name))
    return LABEL_SPECS[name]


@dataclass(frozen=True)
class LabelRequest:
    label: str
    entity_id: str
    related_entity_id: Optional[str] = None

    def __post_init__(self):
        spec = label_spec(self.label)
        identifier(self.entity_id)
        pair = spec.domain in ('entity_pair', 'opposing_player_pair')
        require(pair == (self.related_entity_id is not None), 'Label requires exactly its declared entity arity')
        if self.related_entity_id is not None:
            identifier(self.related_entity_id)


def _unavailable(code: str, reason: str) -> Dict[str, str]:
    return {'code': code, 'reason': reason}


def _position(entity, primary):
    pos = entity['position']['value']
    if pos is None:
        return None
    x, y = pos['x'], pos['y']
    g = primary['geometry']
    if not (0 <= x < g['width'] and 0 <= y < g['height'] and g['playable'][y][x]):
        return None
    return pos


def _entities(primary):
    return {**{p['id']: p for p in primary['players']},
            **{'ball:' + str(i): b for i, b in enumerate(primary['balls'])}}


def _domain(request, primary):
    domain = label_spec(request.label).domain
    players = {p['id']: p for p in primary['players']}
    entities = _entities(primary)
    allowed = {'match': {'match'}, 'team': {'home', 'away'}, 'player': set(players),
               'ball': set(entities) - set(players), 'entity_pair': set(entities),
               'opposing_player_pair': set(players)}[domain]
    if request.entity_id not in allowed or (request.related_entity_id is not None and request.related_entity_id not in allowed):
        return _unavailable('unknown_entity', 'Entity is not in the captured label domain.')
    if domain == 'opposing_player_pair' and players[request.entity_id]['team'] == players[request.related_entity_id]['team']:
        return _unavailable('invalid_pair', 'Block requires opposing players.')
    return None


def _ball_valid(primary):
    if len(primary['balls']) != 1:
        return False
    ball = primary['balls'][0]
    carrier = ball['carrier']['value']
    if not ball['is_carried']:
        return carrier is None
    player = next((p for p in primary['players'] if p['id'] == carrier), None)
    return (player is not None and ball['on_ground'] and not player['status']['in_air']
            and _position(player, primary) is not None
            and player['position'] == ball['position'])


def _block(game, control, request, primary):
    error = _domain(request, primary)
    if error:
        return {'error': error}
    if primary['match']['game_over']:
        return {'error': _unavailable('terminal', 'No prospective block labels at terminal states.')}
    entities = _entities(primary)
    attacker, defender = (entities[ref] for ref in (request.entity_id, request.related_entity_id))
    if any(_position(p, primary) is None for p in (attacker, defender)):
        return {'error': _unavailable('off_pitch', 'Block participants must be on playable pitch.')}
    allowed = {'BLOCK', 'DODGE', 'TACKLE', 'STRIP_BALL', 'SURE_HANDS'}
    if any(set(p['skills']) - allowed for p in primary['players'] if p['position']['present']):
        return {'error': _unavailable('unsupported_state', 'Skill outside the V1 block subset.')}
    if any(not p['status']['up'] or p['status']['stunned'] or p['status']['in_air'] or p['status']['taken_root']
           for p in (attacker, defender)):
        return {'error': _unavailable('unsupported_state', 'Block participants must be standing, unstunned, grounded and unrooted.')}
    if primary['balls'] and not _ball_valid(primary):
        return {'error': _unavailable('unsupported_state', 'Block requires zero or one coherent ball.')}
    a = game.get_player(control.internal_player_id(request.entity_id))
    d = game.get_player(control.internal_player_id(request.related_entity_id))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == dy == 0:
                continue
            square = game.get_square(d.position.x + dx, d.position.y + dy)
            if (game.is_out_of_bounds(square) or not primary['geometry']['playable'][square.y][square.x]
                    or game.get_player_at(square) not in (None, a)):
                return {'error': _unavailable('unsupported_state', 'V1 excludes crowd and occupied defender neighbours.')}
    try:
        result = game.get_block_outcome_probs(a, d, reroll_policy='never', already_rerolled=False)
    except ValueError as error:
        return {'error': _unavailable('unsupported_state', str(error))}
    return {'values': {f: getattr(result, f) for f in _BLOCK_FIELDS}, 'signed_dice': result.signed_dice}


@dataclass(frozen=True, init=False)
class EvaluationContext:
    """Controller-captured bytes only: no Game, RNG object or executable callbacks.

    Capture is part of the pure query. Re-capture explicitly after advancement.
    Privileged JSON can be retained for trusted evaluation callers, never exported
    by the oracle or passed to observe/projectors/readers/views.
    """
    _encoded: bytes = field(repr=False)

    def __init__(self):
        raise TypeError('Use EvaluationContext.capture with trusted controller inputs')

    @classmethod
    def capture(cls, game: Game, control: ObservationControl, time: TimelineContext,
                requests: Sequence[LabelRequest], *, rules: RulesDescriptor,
                privileged: Optional[Dict[str, Any]] = None) -> 'EvaluationContext':
        require(type(time) is TimelineContext, 'Expected TimelineContext')
        validate_time(time.to_json())
        if game.timeline is not None:
            require(time == game.timeline.context, 'Stale or foreign timeline context')
        for field in ('half', 'round'):
            require(getattr(time, field) in (None, getattr(game.state, field)), 'Logical time disagrees with game')
        requests = tuple(requests)
        for request in requests:
            require(type(request) is LabelRequest, 'Expected LabelRequest')
            request.__post_init__()
        require(len(set(requests)) == len(requests), 'Duplicate label request')
        require(type(rules) is RulesDescriptor, 'Supply the episode initial RulesDescriptor')
        validate_rules_descriptor(rules.to_json())
        primary = observe(game, control, 'home').to_json()
        blocks = {}
        for r in requests:
            if r.label.startswith('block.'):
                key = r.entity_id + '/' + r.related_entity_id
                if key not in blocks:
                    blocks[key] = _block(game, control, r, primary)
        captured = object.__new__(cls)
        object.__setattr__(captured, '_encoded', encode_json({
            'primary': primary, 'rules': rules.to_json(), 'context': time.to_json(),
            'requests': [asdict(r) for r in requests], 'blocks': blocks,
            'privileged': {} if privileged is None else privileged}))
        return captured


def _fact(request, primary):
    label = request.label
    entities = _entities(primary)
    if label.startswith('geometry.'):
        a, b = (_position(entities[ref], primary) for ref in (request.entity_id, request.related_entity_id))
        if a is None or b is None:
            return None, _unavailable('off_pitch', 'Geometry requires both entities on playable pitch.')
        dx, dy = abs(a['x'] - b['x']), abs(a['y'] - b['y'])
        return {'manhattan': dx + dy, 'chebyshev': max(dx, dy), 'adjacent': max(dx, dy) == 1}[label.split('.')[1]], None
    if label == 'match.terminal':
        return primary['match']['game_over'], None
    if label.startswith('team.'):
        team = next(t for t in primary['teams'] if t['id'] == request.entity_id)
        field = label.split('.')[1]
        if field == 'possession':
            if not _ball_valid(primary):
                return None, _unavailable('unsupported_state', 'Possession requires exactly one coherent ball.')
            carrier = primary['balls'][0]['carrier']['value']
            return carrier is not None and entities[carrier]['team'] == request.entity_id, None
        return team[field] if field == 'score' else team['resources'][field], None
    entity = entities[request.entity_id]
    field = label.split('.')[1]
    if field == 'position':
        pos = _position(entity, primary)
        return pos, None if pos is not None else _unavailable('off_pitch', 'No playable pitch position.')
    if field == 'carrier':
        if not _ball_valid(primary):
            return None, _unavailable('unsupported_state', 'Carrier requires exactly one coherent ball.')
        return entity['carrier']['value'], None
    return entity[field] if field == 'location' else entity['status'][field], None


def _value(kind, value):
    if kind == 'boolean':
        require(type(value) is bool, 'Expected boolean label')
    elif kind == 'integer':
        integer(value)
    elif kind in ('probability', 'signed_score'):
        require(type(value) in (int, float) and (-1 if kind == 'signed_score' else 0) <= value <= 1,
                'Label outside numeric domain')
    elif kind == 'position':
        keys(value, ('x', 'y'))
        for coordinate in value.values():
            integer(coordinate)
    elif kind == 'location':
        require(value in ('pitch', 'reserves', 'ko', 'casualties', 'dungeon', 'unplaced'), 'Invalid location')
    elif kind == 'nullable_player_id':
        if value is not None:
            _entity_ref(value, 'player')
    elif kind == 'text':
        require(type(value) is str and 0 < len(value.strip()) <= 4096, 'Expected bounded annotation')


def _entity_ref(value, domain):
    identifier(value)
    valid = {'match': value == 'match', 'team': value in ('home', 'away'),
             'player': bool(re.fullmatch(r'(home|away):(0|[1-9][0-9]*)', value)),
             'ball': bool(re.fullmatch(r'ball:(0|[1-9][0-9]*)', value))}
    valid['entity_pair'] = valid['player'] or valid['ball']
    valid['opposing_player_pair'] = valid['player']
    require(valid[domain], 'Invalid entity reference for label domain')


def _external_provenance(spec, value, provenance):
    keys(provenance, ('policy', 'horizon', 'method', 'uncertainty'))
    for field in ('policy', 'method'):
        require(type(provenance[field]) is str and 0 < len(provenance[field].strip()) <= 512,
                'External policy and method must be explicit')
    horizon = provenance['horizon']
    keys(horizon, ('kind', 'decisions'))
    require(horizon['kind'] in ('terminal', 'decisions'), 'Unknown logical horizon')
    if horizon['kind'] == 'terminal':
        require(horizon['decisions'] is None, 'Terminal horizon has no decision count')
    else:
        integer(horizon['decisions'])
    uncertainty = provenance['uncertainty']
    if spec.category == 'estimated':
        require(horizon['kind'] == 'terminal', 'Win probability requires terminal horizon')
        keys(uncertainty, ('kind', 'lower', 'upper', 'coverage', 'method'))
        require(uncertainty['kind'] == 'interval', 'Estimate requires an uncertainty interval')
        for field in ('lower', 'upper', 'coverage'):
            _value('probability', uncertainty[field])
        require(uncertainty['lower'] <= value <= uncertainty['upper'] and uncertainty['coverage'] > 0,
                'Invalid uncertainty interval/coverage')
        require(type(uncertainty['method']) is str and 0 < len(uncertainty['method'].strip()) <= 512,
                'Uncertainty method is required')
    else:
        keys(uncertainty, ('kind', 'reason'))
        require(uncertainty['kind'] == 'not_quantified' and type(uncertainty['reason']) is str
                and 0 < len(uncertainty['reason'].strip()) <= 512,
                'Heuristic/human uncertainty must be explicitly unquantified with reason')


@dataclass(frozen=True, init=False)
class EvaluationRecord(_Record):
    """Validated canonical JSON sidecar; every to_json call returns detached data."""

    def to_json(self) -> Dict[str, Any]:
        return super().to_json()

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> 'EvaluationRecord':
        return cls(data)

    @property
    def spec(self) -> LabelSpec:
        return label_spec(self.to_json()['label']['name'])

    @property
    def value(self) -> LabelValue:
        return self.to_json()['value']

    @property
    def unavailability(self) -> Optional[Unavailability]:
        reason = self.to_json()['unavailability']
        return None if reason is None else Unavailability(**reason)

    @property
    def context(self) -> TimelineContext:
        return TimelineContext(**self.to_json()['context'])

    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'label', 'entity_id', 'related_entity_id', 'context',
                    'available_at', 'rules', 'status', 'value', 'unavailability', 'provenance'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1, 'Unknown evaluation record version')
        require(type(data['label']) is dict, 'Expected LabelSpec metadata')
        spec = label_spec(data['label'].get('name'))
        require(encode_json(data['label']) == encode_json(spec.to_json()), 'Label metadata differs from code-owned specification')
        request = LabelRequest(spec.name, data['entity_id'], data['related_entity_id'])
        # Unknown entities may be reported, but never accepted as available values.
        if data['status'] == 'available':
            _entity_ref(request.entity_id, spec.domain)
            if request.related_entity_id is not None:
                _entity_ref(request.related_entity_id, spec.domain)
            if spec.domain == 'opposing_player_pair':
                require(request.entity_id.split(':')[0] != request.related_entity_id.split(':')[0], 'Expected opposing sides')
        for field in ('context', 'available_at'):
            validate_time(data[field])
        at, ctx = data['available_at'], data['context']
        require(all(at[f] == ctx[f] for f in ('episode_id', 'branch_id')), 'Foreign availability context')
        require(all(at[f] >= ctx[f] for f in ('event_seq', 'decision_seq')), 'Availability predates target')
        validate_rules_descriptor(data['rules'])
        external = spec.origin == 'external'
        require(data['status'] in ('available', 'unavailable'), 'Unknown availability status')
        if data['status'] == 'unavailable':
            require(data['value'] is None and data['provenance'] is None, 'Unavailable labels cannot claim values/provenance')
            reason = data['unavailability']
            keys(reason, ('code', 'reason'))
            require(reason['code'] in ('unknown_entity', 'invalid_pair', 'off_pitch', 'terminal',
                                      'unsupported_state', 'external_required'), 'Unknown unavailability reason')
            require(type(reason['reason']) is str and 0 < len(reason['reason']) <= 4096, 'Unavailability needs reason')
            require(at == ctx, 'Unavailable query must use captured time')
        else:
            require(data['unavailability'] is None, 'Available value cannot carry unavailability')
            _value(spec.value_type, data['value'])
            if external:
                _external_provenance(spec, data['value'], data['provenance'])
            else:
                require(at == ctx, 'Exact/fact availability must equal capture time')
                if spec.category == 'state_fact':
                    require(data['provenance'] is None, 'State facts use only code-owned origin')
                else:
                    p = data['provenance']
                    keys(p, ('policy', 'horizon', 'method', 'uncertainty', 'signed_dice'))
                    require(p == _block_provenance(p['signed_dice']), 'Invalid exact-rule provenance')
                    require(type(p['signed_dice']) is int and p['signed_dice'] in (-3, -2, 1, 2, 3), 'Invalid signed dice')

    @property
    def key(self) -> tuple:
        data = self.to_json()
        ctx = data['context']
        return (ctx['episode_id'], ctx['branch_id'], ctx['event_seq'], ctx['decision_seq'],
                data['entity_id'], data['related_entity_id'], data['label']['name'], data['label']['version'])

    @classmethod
    def external(cls, context: EvaluationContext, request: LabelRequest, value: Any, *,
                 available_at: TimelineContext, provenance: Dict[str, Any]) -> 'EvaluationRecord':
        require(type(context) is EvaluationContext and type(request) is LabelRequest, 'Expected evaluation context/request')
        request.__post_init__()
        spec = label_spec(request.label)
        require(spec.origin == 'external', 'External data cannot assert simulator facts or exact quantities')
        data = decode_json(context._encoded)
        require(_domain(request, data['primary']) is None, 'External entity is outside captured domain')
        require(type(available_at) is TimelineContext, 'Expected explicit availability time')
        return cls(_row(data, request, value, None, provenance, available_at.to_json()))


def _block_provenance(dice):
    return {'policy': 'issue8_local_v1/never', 'horizon': 'single_block_direct_effects',
            'method': 'Game.get_block_outcome_probs', 'uncertainty': 'exact_finite_enumeration', 'signed_dice': dice}


def _row(data, request, value, error, provenance=None, available_at=None):
    return {'schema_version': 1, 'label': label_spec(request.label).to_json(),
            'entity_id': request.entity_id, 'related_entity_id': request.related_entity_id,
            'context': data['context'], 'available_at': data['context'] if available_at is None else available_at,
            'rules': data['rules'], 'status': 'unavailable' if error else 'available',
            'value': value, 'unavailability': error, 'provenance': provenance}


class EvaluationOracle:
    """Structural Evaluator[EvaluationContext, Tuple[EvaluationRecord, ...]]."""

    def evaluate(self, context: EvaluationContext, /) -> Tuple[EvaluationRecord, ...]:
        require(type(context) is EvaluationContext, 'Expected detached EvaluationContext')
        data = decode_json(context._encoded)
        records = []
        for raw in data['requests']:
            request = LabelRequest(**raw)
            spec = label_spec(request.label)
            value, provenance = None, None
            error = _domain(request, data['primary'])
            if error is None:
                if spec.origin == 'external':
                    error = _unavailable('external_required', 'Supply explicitly attributed external data.')
                elif spec.category == 'exact_rule_quantity':
                    result = data['blocks'][request.entity_id + '/' + request.related_entity_id]
                    error = result.get('error')
                    if error is None:
                        value = result['values'][request.label.split('.')[1]]
                        provenance = _block_provenance(result['signed_dice'])
                else:
                    value, error = _fact(request, data['primary'])
            if error is None:
                try:
                    _value(spec.value_type, value)
                except RecordError as invalid:
                    value, provenance = None, None
                    error = _unavailable('unsupported_state', str(invalid))
            records.append(EvaluationRecord(_row(data, request, value, error, provenance)))
        return tuple(records)


def evaluation_channel(records: Sequence[EvaluationRecord]) -> Dict[str, Any]:
    """Payload for EpisodeRecorder.append_channel('evaluation', observation_id, ...).

    Existing DATA-02 storage stays inert. Consumers explicitly parse each record;
    arbitrary pre-existing evaluation JSON is not certified by this catalogue.
    """
    rows, seen = [], set()
    for record in records:
        require(type(record) is EvaluationRecord, 'Expected EvaluationRecord')
        row = record.to_json()
        EvaluationRecord.from_json(row)
        require(record.key not in seen, 'Duplicate evaluation key')
        seen.add(record.key)
        rows.append(row)
    return {'labels': {'records': rows}, 'estimates': {},
            'provenance': {'schema': 'EvaluationRecord', 'schema_version': 1}}
