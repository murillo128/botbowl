"""Bounded EVAL-06 operational explanations, extending DATA-02 event envelopes.

No rules are re-executed here. Reports are copied at emission; the few rule
branches with no report supply their evaluated locals explicitly. Trace IDs
have a separate namespace so API-02 counters and sports reports stay unchanged.
"""
from copy import deepcopy

from .records import (EventV1, context, encode_json, identifier,
                      integer, keys, require, validate_rules_descriptor)
from .rules import describe_rules
from .timeline import Timeline


# Closed emission allowlist. A supported procedure does not imply support for
# every skill, report or branch in that procedure. Unlisted signals are explicit
# unsupported events, without invented rule fields.
_REPORTS = {
    'GFI': ('movement.gfi', 'SUCCESSFUL_GFI FAILED_GFI'),
    'Dodge': ('movement.dodge', 'SUCCESSFUL_DODGE FAILED_DODGE'),
    'Pickup': ('movement.pickup', 'SUCCESSFUL_PICKUP FAILED_PICKUP'),
    'Block': ('block.roll', 'BLOCK_ROLL ACTION_SELECT_DIE'),
    'Push': ('block.push', 'PUSHED PUSHED_INTO_CROWD'),
    'FollowUp': ('block.follow_up', 'FOLLOW_UP'),
    'PassAttempt': ('pass.attempt', 'ACCURATE_PASS INACCURATE_PASS FUMBLE'),
    'Intercept': ('pass.intercept', 'INTERCEPTION FAILED_INTERCEPTION'),
    'Catch': ('pass.catch', 'SUCCESSFUL_CATCH FAILED_CATCH'),
    'KnockDown': ('injury.knock_down', 'KNOCKED_DOWN'),
    'Armor': ('injury.armor', 'ARMOR_BROKEN ARMOR_NOT_BROKEN'),
    'Casualty': ('injury.casualty', 'CASUALTY DECAYING BADLY_HURT MISS_NEXT_GAME DEAD'),
    'Apothecary': ('injury.apothecary', 'APOTHECARY_USED_KO KNOCKED_OUT CASUALTY_APOTHECARY '
                    'APOTHECARY_USED_CASUALTY BADLY_HURT MISS_NEXT_GAME DEAD'),
    'KnockOut': ('injury.knock_out', 'KNOCKED_OUT'),
    'Regeneration': ('injury.regeneration', 'SUCCESSFUL_REGENERATION FAILED_REGENERATION'),
    'Reroll': ('reroll.resolve', 'REROLL_USED SKILL_USED'),
    'Pro': ('reroll.pro', 'SUCCESSFUL_PRO FAILED_PRO'),
    'Loner': ('reroll.loner', 'SUCCESSFUL_LONER FAILED_LONER'),
    'Turnover': ('turn.turnover', 'TURNOVER'),
    'Touchdown': ('drive.touchdown', 'TOUCHDOWN'),
}
_SIGNALS = {
    'Move': ('movement.move', ('checks', 'moved')),
    'Injury': ('injury.table', ('casualty', 'knock_out', 'stunned', 'ball_and_chain_ko')),
    'Block': ('block.resolve', ('resolved_face',)),
    'FollowUp': ('block.follow_up', ('stayed',)),
    'Push': ('block.push', ('stopped',)),
    'Reroll': ('reroll.resolve', ('resolved',)),
    'PassAttempt': ('pass.attempt', ('interceptors',)),
}
_DECISIONS = {
    'MoveAction': 'movement.action', 'BlitzAction': 'movement.action',
    'PassAction': 'pass.action', 'BlockAction': 'block.action',
    'Dodge': 'movement.dodge', 'Block': 'block.select', 'Push': 'block.push',
    'FollowUp': 'block.follow_up', 'Interception': 'pass.select_interceptor',
    'Apothecary': 'injury.apothecary', 'Reroll': 'reroll.choice', 'Turn': 'turn.choice',
}
_PHASES = ('half_started', 'round_started', 'drive_started', 'drive_ended',
           'team_turn_started', 'team_turn_ended', 'activation_started', 'activation_ended')
# The engine's existing reports repeat historical dice for selection/application.
# Only these emissions introduce a newly consumed roll.
_NEW_ROLLS = frozenset('SUCCESSFUL_GFI FAILED_GFI SUCCESSFUL_DODGE FAILED_DODGE '
    'SUCCESSFUL_PICKUP FAILED_PICKUP BLOCK_ROLL ACCURATE_PASS INACCURATE_PASS FUMBLE '
    'INTERCEPTION FAILED_INTERCEPTION SUCCESSFUL_CATCH FAILED_CATCH '
    'ARMOR_BROKEN ARMOR_NOT_BROKEN CASUALTY DECAYING CASUALTY_APOTHECARY '
    'SUCCESSFUL_REGENERATION FAILED_REGENERATION SUCCESSFUL_PRO FAILED_PRO '
    'SUCCESSFUL_LONER FAILED_LONER'.split())


def coverage_registry():
    """Fresh closed registry of procedure/report/decision/explicit-signal sites."""
    result = {}
    for name in _EMITTERS:
        reports = _REPORTS.get(name)
        signal = _SIGNALS.get(name)
        result[name] = {
            'status': 'partial' if reports or signal or name in _DECISIONS else 'unsupported',
            'reports': [] if reports is None else reports[1].split(),
            'signals': [] if signal is None else list(signal[1]),
            'decisions': name in _DECISIONS,
        }
    # No runtime class discovery expands support. This check is diagnostic only.
    result['external'] = {'status': 'unsupported', 'reports': [], 'signals': [], 'decisions': False}
    result['timeline'] = {'status': 'instrumented', 'signals': list(_PHASES), 'reports': [], 'decisions': False}
    return {'schema_version': 1, 'emitters': result, 'unknown_emitter': 'unsupported',
            'scope': 'operational; no tactical utility or identified counterfactual effects'}


def _trace_id(value):
    require(type(value) is list and len(value) == 4 and value[2] == 'rule', 'Invalid rule event ID')
    identifier(value[0])
    identifier(value[1])
    integer(value[3], 1)


def validate_trace_status(row):
    keys(row, ('schema_version', 'complete', 'reason', 'retained_events', 'retained_bytes',
               'context', 'rules', 'coverage_version', 'roll_channel'))
    require(type(row['schema_version']) is int and row['schema_version'] == 1 and
            type(row['coverage_version']) is int and row['coverage_version'] == 1, 'Unknown trace status version')
    require(type(row['complete']) is bool and row['complete'] == (row['reason'] is None), 'Invalid trace completeness')
    require(row['reason'] in (None, 'consumer_failure', 'capture_failure', 'event_limit', 'byte_limit',
                              'timeline_restored'), 'Unknown trace failure reason')
    require(row['roll_channel'] == 'public_history', 'Unknown roll channel')
    integer(row['retained_events'])
    integer(row['retained_bytes'])
    context(row['context'])
    validate_rules_descriptor(row['rules'])


def validate_rule_event(row):
    """Payload V2 of EventV1; context is an API-02 prefix, ID is namespaced."""
    keys(row, ('schema_version', 'payload_version', 'event_id', 'context', 'decision_seq', 'kind', 'data'))
    require(type(row['schema_version']) is int and row['schema_version'] == 1 and
            type(row['payload_version']) is int and row['payload_version'] == 2, 'Unknown explanation version')
    require(row['kind'] in ('rule', 'rule_decision'), 'Invalid explanation kind')
    ctx = row['context']
    context(ctx)
    _trace_id(row['event_id'])
    require(row['event_id'][:2] == [ctx['episode_id'], ctx['branch_id']], 'Foreign rule event ID')
    data = row['data']
    require(type(data) is dict, 'Expected explanation object')
    common = ('coverage', 'emitter', 'decision_id', 'parent_event_ids', 'event_ref')
    require(data.get('coverage') in ('instrumented', 'unsupported'), 'Unknown coverage')
    expected = common if data['coverage'] == 'unsupported' else common + (
        'rule_id', 'participants', 'conditions', 'modifiers', 'threshold', 'rolls', 'outcome')
    keys(data, expected)
    identifier(data['emitter'])
    if row['decision_seq'] is None:
        require(data['decision_id'] is None and row['kind'] != 'rule_decision', 'Missing decision cause')
    else:
        integer(row['decision_seq'], 1)
        require(row['decision_seq'] == ctx['decision_seq'] and data['decision_id'] ==
                [ctx['episode_id'], ctx['branch_id'], row['decision_seq']], 'Invalid rule decision ID')
    ref = data['event_ref']
    if ref is not None:
        require(type(ref) is list and ref == [ctx['episode_id'], ctx['branch_id'], ctx['event_seq']]
                and ctx['event_seq'] > 0, 'Invalid report/phase anchor')
    parents = data['parent_event_ids']
    require(type(parents) is list, 'Expected parent list')
    seen = set()
    for parent in parents:
        _trace_id(parent)
        require(parent[:2] == row['event_id'][:2] and parent[3] < row['event_id'][3],
                'Future, cyclic or cross-branch rule edge')
        require(tuple(parent) not in seen, 'Duplicate rule edge')
        seen.add(tuple(parent))
    if data['coverage'] == 'unsupported':
        return
    require(type(data['rule_id']) is str and ':' in data['rule_id'], 'Invalid rule reference')
    emitter = data['emitter']
    name = data['outcome']['type']
    expected_rules = set()
    if row['kind'] == 'rule_decision' and emitter in _DECISIONS:
        expected_rules.add(_DECISIONS[emitter])
    elif row['kind'] == 'rule':
        if emitter in _REPORTS and name in _REPORTS[emitter][1].split():
            expected_rules.add(_REPORTS[emitter][0])
        if emitter in _SIGNALS and name in _SIGNALS[emitter][1]:
            expected_rules.add(_SIGNALS[emitter][0])
        if emitter == 'timeline' and name in _PHASES:
            expected_rules.add('timeline.' + name)
    require(data['rule_id'].split(':', 1)[1] in expected_rules, 'Rule outside closed coverage registry')
    require(type(data['participants']) is dict, 'Expected participants')
    for role, entity in data['participants'].items():
        require(role in ('player', 'opponent', 'team', 'actor', 'attacker', 'defender', 'pusher',
                         'passer', 'catcher', 'interceptor', 'inflictor', 'selected_player'), 'Unknown participant role')
        require(type(entity) is str and (entity in ('home', 'away') or
                entity.split(':')[0] in ('home', 'away') and entity.split(':')[-1].isdigit() and
                entity.count(':') == 1), 'Invalid participant ID')
    require(type(data['conditions']) is dict, 'Expected evaluated conditions')
    # Conditions are finite, inert public scalar/list values supplied at sites;
    # they are not an extensible object/RNG serialization channel.
    for name, value in data['conditions'].items():
        require(name in _CONDITIONS, 'Unknown evaluated condition')
        require(value is None or type(value) in (bool, int, str, list), 'Invalid condition value')
        if type(value) is list:
            require(all(type(v) in (bool, int, str) for v in value), 'Invalid condition list')
    require(data['modifiers'] is None or type(data['modifiers']) is int, 'Invalid modifier total')
    threshold = data['threshold']
    if threshold is not None:
        keys(threshold, ('target', 'target_higher', 'target_lower', 'highest_succeed', 'lowest_fail'))
        require(threshold['target'] is None or type(threshold['target']) is int, 'Invalid threshold')
        for field in ('target_higher', 'target_lower', 'highest_succeed', 'lowest_fail'):
            require(type(threshold[field]) is bool, 'Invalid comparison flag')
    keys(data['outcome'], ('type', 'n', 'position', 'skill'))
    # Reuse DATA-02's closed public outcome/dice validator, including redaction.
    outcome = data['outcome']
    report = {'outcome_type': 'BLOCK_ROLL', 'n': outcome['n'], 'pos': outcome['position'],
              'skill': outcome['skill'], 'player_id': None, 'opp_player_id': None, 'team_id': None,
              'rolls': data['rolls']}
    identifier(outcome['type'])
    anchor = dict(ctx, event_seq=max(1, ctx['event_seq']))
    EventV1({'schema_version': 1, 'payload_version': 1, 'kind': 'report', 'context': anchor,
             'event_id': [ctx['episode_id'], ctx['branch_id'], anchor['event_seq']],
             'decision_seq': row['decision_seq'], 'data': report})


_CONDITIONS = frozenset('weather accurate handoff diving pass_distance ttm blitz frenzy_block '
    'dauntless_success favor dice_count gfi dodge from_x from_y to_x to_y '
    'foul in_crowd stab mighty_blow_used dirty_player_used thick_skull stunty mighty_blow '
    'dirty_player niggling casualty_total casualty_threshold ko_total ko_threshold '
    'armor_roll injury_roll turnover chain knock_down crowd strip_ball_condition '
    'can_use_team_reroll can_use_pro skill use_reroll selected_die '
    'attacker_block defender_block defender_dodge attacker_tackle '
    'fend frenzy taken_root apothecaries outcome decay decay_roll blood_lust always_hungry '
    'interceptor_count action target_higher target_lower highest_succeed lowest_fail '
    'reason turn_kind action_type tackle_zones ignore_opp_mods prehensile_tails '
    'include_diving_tackle interception armor_broken armor_total claws claws_total '
    'claws_threshold claws_comparison claws_threshold_met'.split())


def _validate_sports_anchor(row, anchor):
    """Compare mirrored facts only; report roll reuse is not new consumption."""
    data, sports = row['data'], anchor['data']
    require(row['kind'] == 'rule', 'Optional decision has a sports anchor')
    if anchor['kind'] == 'report':
        entry = _REPORTS.get(data['emitter'])
        require(entry is not None and data['rule_id'].split(':', 1)[1] == entry[0] and
                sports['outcome_type'] in entry[1].split(), 'Rule site mismatches sports anchor')
        outcome = {'type': sports['outcome_type'], 'n': sports['n'],
                   'position': sports['pos'], 'skill': sports['skill']}
    else:
        require(data['emitter'] == 'timeline' and anchor['kind'] in _PHASES and
                data['rule_id'].split(':', 1)[1] == 'timeline.' + anchor['kind'],
                'Rule site mismatches sports anchor')
        outcome = {'type': anchor['kind'], 'n': None, 'position': None, 'skill': None}
        require(data['conditions'] == {k: v for k, v in sports.items() if k in _CONDITIONS},
                'Phase conditions mismatch sports anchor')
    require(data['outcome'] == outcome, 'Realized outcome mismatches sports anchor')
    for source, role in (('player_id', 'player'), ('opp_player_id', 'opponent'), ('team_id', 'team')):
        if sports.get(source) is not None:
            require(data['participants'].get(role) == sports[source], 'Participant mismatches sports anchor')


def validate_rule_graph(events, *, rules=None, sports_events=None, decisions=None, players=None):
    """Linear identity/reference validation; no inferred gameplay or text parsing."""
    seen = {}
    seen_decisions = set()
    seen_anchors = set()
    anchors = None if sports_events is None else {tuple(e['event_id']): e for e in sports_events}
    choices = None if decisions is None else {tuple(d['transition_id']): d for d in decisions}
    previous = None
    for index, row in enumerate(events, 1):
        EventV1(row)
        require(row['payload_version'] == 2, 'Expected rule trace events')
        require(row['event_id'][3] == index, 'Rule IDs have gaps/duplicates')
        ctx, data = row['context'], row['data']
        if previous is not None:
            require(ctx['episode_id'] == previous['episode_id'] and
                    ctx['event_seq'] >= previous['event_seq'] and ctx['decision_seq'] >= previous['decision_seq'],
                    'Rule contexts move backwards or cross episodes')
        for parent in data['parent_event_ids']:
            require(tuple(parent) in seen, 'Missing rule antecedent')
        if anchors is not None and data['event_ref'] is not None:
            anchor = anchors.get(tuple(data['event_ref']))
            require(anchor is not None and anchor['context'] == ctx and
                    anchor['decision_seq'] == row['decision_seq'], 'Missing or inconsistent sports anchor')
            require(tuple(data['event_ref']) not in seen_anchors, 'Duplicate sports anchor')
            seen_anchors.add(tuple(data['event_ref']))
            if data['coverage'] == 'instrumented':
                _validate_sports_anchor(row, anchor)
        if row['kind'] == 'rule_decision':
            require(tuple(data['decision_id']) not in seen_decisions, 'Duplicate optional decision event')
            seen_decisions.add(tuple(data['decision_id']))
        if choices is not None and data['decision_id'] is not None:
            choice = choices.get(tuple(data['decision_id']))
            require(choice is not None and choice['before']['event_seq'] <= ctx['event_seq'] <=
                    choice['after']['event_seq'], 'Missing or inconsistent decision anchor')
            if row['kind'] == 'rule_decision' and data['coverage'] == 'instrumented':
                require(data['participants'].get('actor') == choice['actor_id'] and
                        data['outcome']['type'] == choice['action']['type'], 'Mismatched optional decision')
        if data['coverage'] == 'instrumented':
            if rules is not None:
                require(data['rule_id'].startswith(rules['ruleset_id'] + ':'), 'Foreign rule descriptor')
            if players is not None:
                require(all(v in ('home', 'away') or v in players for v in data['participants'].values()),
                        'Missing participant')
        seen[tuple(row['event_id'])] = row
        previous = ctx


class RuleTrace:
    """Opt-in public historical trace; failures stop capture, never engine advance.

    Attach before init, optionally after EpisodeRecorder. Limits include retained
    rows and bookkeeping. A failed consumer receives no retries. Read status even
    for zero rows; `complete` means capture through this prefix, not full rules.
    """
    def __init__(self, game, *, rules=None, max_events=10000, max_bytes=16 * 1024 * 1024, consumer=None):
        for value in (max_events, max_bytes):
            integer(value)
        if game.rule_trace is not None or game.state.reports or game.start_time is not None:
            raise ValueError('Attach one rule trace before init')
        if rules is None:
            rules = describe_rules(game.config, game.ruleset, game.arena,
                                   game.state.home_team, game.state.away_team).to_json()
        validate_rules_descriptor(rules)
        self.game = game
        self.timeline = game.timeline or Timeline(game)
        self._rules = deepcopy(rules)
        self.max_events, self.max_bytes = max_events, max_bytes
        self.consumer = consumer
        self._events = []
        self._bytes = 0
        self._nodes = {}
        self._active = None
        self._failure = None
        game.rule_trace = self

    @property
    def events(self):
        return deepcopy(self._events)

    @property
    def rules(self):
        return deepcopy(self._rules)

    @property
    def status(self):
        return {'schema_version': 1, 'complete': self._failure is None, 'reason': self._failure,
                'retained_events': len(self._events), 'retained_bytes': self._bytes,
                'context': self.timeline.context.to_json(), 'rules': self.rules,
                'coverage_version': 1, 'roll_channel': 'public_history'}

    def _stop(self, reason):
        if self._failure is None:
            self._failure = reason
        self._nodes.clear()

    def _capture(self, operation, *args, **kwargs):
        if self._failure is None:
            try:
                return operation(*args, **kwargs)
            except Exception:
                # No exception text, stack, callback object, RNG or future state.
                self._stop('consumer_failure' if operation == self._deliver else 'capture_failure')

    def _node(self, proc):
        if proc not in self._nodes:
            require(len(self._nodes) < max(1, self.max_events), 'Trace bookkeeping limit')
            self._nodes[proc] = {'parents': [], 'creator': None, 'rolls': []}
        return self._nodes[proc]

    def _enter(self, proc, method, action):
        node = self._node(proc)
        creator = node['creator']
        if creator in self._nodes:
            node['parents'] = list(dict.fromkeys(node['parents'] + self._nodes[creator]['parents']))
        if method == 'step' and action is not None and self.timeline._pending is not None:
            choice = self.timeline._decisions[self.timeline._pending].action
            emitter = type(proc).__name__
            participants = self._participants(proc)
            participants['actor'] = choice.actor_id
            if choice.player_id is not None:
                participants['selected_player'] = choice.player_id
            conditions = {'action': choice.type}
            if emitter == 'Apothecary':
                conditions.update(apothecaries=proc.player.team.state.apothecaries,
                                  outcome=proc.outcome.name, decay=proc.decay, decay_roll=proc.decay_roll)
            elif emitter == 'Reroll':
                conditions.update(can_use_team_reroll=proc.can_use_team_reroll, can_use_pro=proc.can_use_pro)
            self._emit(emitter, _DECISIONS.get(emitter), choice.type, participants=participants,
                       conditions=conditions, kind='rule_decision', position=(
                           None if choice.position is None else choice.position.to_json()))

    def _leave(self, proc, before):
        node = self._node(proc)
        for child in self.game.state.stack.items:
            if child not in before and child is not proc:
                entry = self._node(child)
                entry['creator'] = proc
                entry['parents'] = node['parents'][:]
        creator = node['creator']
        if creator in self._nodes:
            self._nodes[creator]['parents'] = node['parents'][:]

    def _call(self, proc, method, action=None):
        if self._failure is not None:
            return getattr(proc, method)(action) if method == 'step' else getattr(proc, method)()
        previous = self._active
        self._active = proc
        before = tuple(self.game.state.stack.items)
        self._capture(self._enter, proc, method, action)
        try:
            return getattr(proc, method)(action) if method == 'step' else getattr(proc, method)()
        finally:
            self._capture(self._leave, proc, before)
            self._active = previous

    def _participants(self, proc, outcome=None):
        entities = self.timeline._entities
        result = {}
        for name in ('player', 'attacker', 'defender', 'pusher', 'passer', 'catcher', 'interceptor', 'inflictor'):
            player = getattr(proc, name, None)
            if player is not None:
                result[name] = entities._player(player)
        if outcome is not None:
            for role, player in (('player', outcome.player), ('opponent', outcome.opp_player)):
                if player is not None:
                    result[role] = entities._player(player)
            if outcome.team is not None:
                result['team'] = entities._team(outcome.team)
        return result

    def _deliver(self, row):
        if self.consumer is not None:
            self.consumer(deepcopy(row))

    def _emit(self, emitter, rule, outcome, *, conditions=None, rolls=(), participants=None,
              kind='rule', event_ref=None, n=None, position=None, skill=None, threshold=None):
        if len(self._events) >= self.max_events:
            self._stop('event_limit')
            return
        ctx = self.timeline.context.to_json()
        cause = ctx['decision_seq'] if self.timeline._pending is not None else None
        node = self._node(self._active)
        parents = [list(p) for p in node['parents'] if list(p[:2]) == [ctx['episode_id'], ctx['branch_id']]]
        data = {'coverage': 'unsupported' if rule is None else 'instrumented', 'emitter': emitter,
                'decision_id': None if cause is None else [ctx['episode_id'], ctx['branch_id'], cause],
                'parent_event_ids': parents, 'event_ref': event_ref}
        if rule is not None:
            consumed = []
            for roll in rolls:
                if all(roll is not old for old in node['rolls']):
                    node['rolls'].append(roll)
                    consumed.append(roll.to_json())
            tested = rolls[-1].to_json() if rolls else None
            data.update(rule_id=self._rules['ruleset_id'] + ':' + rule,
                        participants=participants or self._participants(self._active),
                        conditions=conditions or {}, modifiers=None if tested is None or
                        tested['roll_type'] == 'BLOCK_ROLL' else tested['modifiers'],
                        threshold=threshold if threshold is not None else (
                            None if tested is None or tested['target'] is None else {key: tested[key] for key in (
                                'target', 'target_higher', 'target_lower', 'highest_succeed', 'lowest_fail')}),
                        rolls=consumed, outcome={'type': outcome, 'n': n, 'position': position, 'skill': skill})
        row = {'schema_version': 1, 'payload_version': 2, 'kind': kind, 'context': ctx,
               'event_id': [ctx['episode_id'], ctx['branch_id'], 'rule', len(self._events) + 1],
               'decision_seq': cause, 'data': data}
        row = EventV1(row).to_json()
        size = len(encode_json(row)) + 1
        if self._bytes + size > self.max_bytes:
            self._stop('byte_limit')
            return
        self._events.append(row)
        self._bytes += size
        node['parents'] = [tuple(row['event_id'])]
        self._capture(self._deliver, row)

    def report(self, outcome):
        self._capture(self._report, outcome)

    def _report(self, outcome):
        emitter = 'external' if self._active is None else type(self._active).__name__
        entry = _REPORTS.get(emitter)
        name = outcome.outcome_type.name
        rule = entry[0] if entry and name in entry[1].split() else None
        proc = self._active
        # Balls only. Other pieces/skill-specific reports remain unsupported.
        if emitter in ('PassAttempt', 'Catch') and type(proc.piece).__name__ != 'Ball':
            rule = None
        if emitter == 'Intercept' and type(proc.ball).__name__ != 'Ball':
            rule = None
        conditions = self._node(proc).pop('conditions', {})
        if rule:
            for field in ('accurate', 'handoff', 'diving', 'blitz', 'frenzy_block', 'dauntless_success',
                          'foul', 'in_crowd', 'stab', 'chain', 'knock_down', 'crowd', 'strip_ball_condition',
                          'armor_roll', 'injury_roll', 'turnover', 'decay', 'decay_roll', 'blood_lust', 'always_hungry'):
                if hasattr(proc, field):
                    conditions[field] = getattr(proc, field)
            if emitter in ('GFI', 'PassAttempt', 'Catch', 'Pickup'):
                conditions['weather'] = self.game.state.weather.name
            if emitter == 'PassAttempt':
                conditions['pass_distance'] = proc.pass_distance.name
            if emitter == 'Block':
                conditions['favor'] = self.timeline._entities._team(proc.favor)
                conditions['dice_count'] = len(proc.roll.dice)
        rolls = list(outcome.rolls or []) if name in _NEW_ROLLS else []
        if name == 'CASUALTY_APOTHECARY':
            rolls = rolls[-1:]  # The first candidate was consumed by Casualty.
        if emitter == 'Casualty' and name in ('CASUALTY', 'DECAYING'):
            rolls = [proc.roll]  # Fixed results still consume D68 in this engine.
        ctx = self.timeline.context
        self._emit(emitter, rule, name, conditions=conditions, rolls=rolls,
                   threshold=self._node(proc).pop('threshold', None),
                   participants=self._participants(proc, outcome) if rule else {},
                   event_ref=[ctx.episode_id, ctx.branch_id, ctx.event_seq],
                   n=outcome.n.name if hasattr(outcome.n, 'name') else outcome.n,
                   position=None if outcome.position is None else outcome.position.to_json(),
                   skill=None if outcome.skill is None else outcome.skill.name)

    def conditions(self, emitter, values, *, threshold=None):
        # Modifier helpers also serve queries/observations. Capture only during
        # this exact procedure's execution, never while inspecting legal moves.
        if type(self._active).__name__ == emitter:
            def capture():
                node = self._node(self._active)
                node.setdefault('conditions', {}).update(values)
                if threshold is not None:
                    node['threshold'] = threshold
            self._capture(capture)

    def signal(self, proc, name, conditions, rolls):
        def emit():
            entry = _SIGNALS.get(type(proc).__name__)
            require(entry is not None and name in entry[1], 'Unknown rule site')
            rule = entry[0]
            if type(proc).__name__ == 'PassAttempt' and type(proc.piece).__name__ != 'Ball':
                rule = None
            self._emit(type(proc).__name__, rule, name, conditions=conditions, rolls=rolls)
        self._capture(emit)

    def phase(self, kind, data):
        def emit():
            ctx = self.timeline.context
            participants = {key[:-3]: value for key, value in data.items()
                            if key in ('team_id', 'player_id') and value is not None}
            self._emit('timeline', 'timeline.' + kind, kind,
                       conditions={key: value for key, value in data.items() if key in _CONDITIONS},
                       participants=participants, event_ref=[ctx.episode_id, ctx.branch_id, ctx.event_seq])
        self._capture(emit)


# Reviewed engine emitter inventory; additions default to unsupported.
_EMITTERS = (
    'Regeneration', 'Apothecary', 'Armor', 'Stab', 'FoulAppearance',
    'Block', 'Bounce', 'Casualty', 'Catch', 'Intercept',
    'CoinTossFlip', 'CoinTossKickReceive', 'Ejection', 'Foul', 'ResetHalf',
    'Half', 'Injury', 'Interception', 'Touchback', 'LandKick',
    'Fans', 'Kickoff', 'GetTheRef', 'Riot', 'HighKick',
    'CheeringFans', 'BrilliantCoaching', 'ThrowARock', 'PitchInvasionRoll', 'KickoffTable',
    'KnockDown', 'KnockOut', 'Leap', 'Shadowing', 'Tentacles',
    'Move', 'GFI', 'Dodge', 'TurnoverIfPossessionLost', 'Handoff',
    'Explode', 'Land', 'PassAttempt', 'Pickup', 'StandUp',
    'PlaceBall', 'EndPlayerTurn', 'JumpUpToBlock', 'EscapeBeingEaten', 'AlwaysHungry',
    'UndoPlayerAction', 'MoveAction', 'HandoffAction', 'PassAction', 'ThrowBombAction',
    'FoulAction', 'BlockAction', 'Frenzy', 'BlitzAction', 'StartGame',
    'EndGame', 'Pregame', 'PreKickoff', 'FollowUp', 'Push',
    'Scatter', 'ClearBoard', 'Setup', 'ThrowIn', 'Turnover',
    'Touchdown', 'TurnStunned', 'EndTurn', 'Turn', 'WeatherTable',
    'Negatrait', 'Bonehead', 'ReallyStupid', 'WildAnimal', 'TakeRoot',
    'BloodLustBlockOrMove', 'BloodLust', 'Reroll', 'Pro', 'Loner',
    'EatThrall', 'HypnoticGaze',
)
