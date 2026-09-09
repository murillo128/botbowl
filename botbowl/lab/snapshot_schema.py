"""Closed field contracts for SnapshotFileV1; never inferred from file contents.

Specs are data consumed by snapshot_io's pre-allocation validator. Every entry is
required unless LAZY explicitly declares its creation phase. Inheritance is
resolved against the trusted codec classes; no unknown field receives a default.
"""


def choice(*specs):
    return ('or', *specs)


def optional(spec):
    return choice('null', spec)


def sequence(spec, kinds='list rlist tuple'):
    return ('items', kinds, spec)


def mapping(key, value):
    return ('map', key, value)


def record(**fields):
    return ('record', fields)


def product(*specs):
    return ('product', *specs)


def integer(low=None, high=None):
    return ('integer', low, high)


def literal(*values):
    return ('literal', *values)


def enum(name):
    return 'enum/' + name


def model(name):
    return 'model/' + name


def proc(name):
    return 'procedure/' + name


def data(name):
    return 'data/' + name


def group(spec, names):
    return dict.fromkeys(names.split(), spec)


BOOL = 'bool'
INT = integer()
NAT = integer(0)
POS = integer(1)
NUMBER = 'number'
TIME = ('number', 0, None)
TEXT = 'str'
NAME = ('text', r'[\s\S]+')
IDENTIFIER = ('text', r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
SIDE = literal('home', 'away')
PLAYER = model('Player')
TEAM = model('Team')
SQUARE = model('Square')
ROLL = model('DiceRoll')
BALL = model('Ball')
PIECE = choice(BALL, model('Bomb'), PLAYER)
SKILL = enum('Skill')
PLAYERS = sequence(PLAYER)
SQUARES = sequence(SQUARE)
TEAMS = sequence(TEAM)
ROLLS = sequence(ROLL)
SKILLS = sequence(SKILL, 'list rlist tuple set rset frozenset')
STRINGS = sequence(TEXT)
CHOICES = sequence(model('ActionChoice'))
INJURIES = sequence(enum('CasualtyEffect'))
REROLL = optional(proc('Reroll'))
GRAPH = 'graph'  # Explicit Procedure.context extension slot, validated node by node.
JSON = 'json'  # Data records only; no engine references, bytes, enums or cycles.
EMPTY_PATHS = ('empty', 'dict rdict')


# The finite producer contract from issue #39's design closure. These are
# thresholds, not die outcomes; path HANDOFF/FOUL annotations are flat.
D6_TARGET = integer(2, 6)
FOUL_TARGET = integer(2, 12)
ARMOR_TARGET = integer(1, 12)
EMPTY_TARGETS = ('empty', 'list rlist tuple')
ACTION_TARGETS = {
    **dict.fromkeys(('MOVE STAND_UP LEAP PICKUP_TEAM_MATE BLOCK PASS THROW_TEAM_MATE '
                     'THROW_BOMB HYPNOTIC_GAZE SELECT_PLAYER').split(), sequence(sequence(D6_TARGET))),
    'HANDOFF': choice(sequence(sequence(D6_TARGET)), sequence(D6_TARGET)),
    'FOUL': choice(sequence(sequence(FOUL_TARGET)), sequence(FOUL_TARGET)),
    'STAB': sequence(('suffix', D6_TARGET, ARMOR_TARGET)),
}


LOCAL = {
    'Reversible': {'_trajectory': optional('Trajectory'), '_ignored_keys': sequence(TEXT, 'set')},
    'Piece': {'position': optional(SQUARE)},
    'Catchable': {'on_ground': BOOL, 'is_carried': BOOL},
    'TimeLimits': group(optional(TIME), 'turn secondary init end'),
    'Configuration': {
        **group(TEXT, 'name arena ruleset'),
        **group(NAT, 'roster_size pitch_min scrimmage_min wing_max'),
        'pitch_max': literal(1, 3, 5, 7, 11), 'rounds': POS,
        **group(BOOL, 'kick_off_table fast_mode debug_mode competition_mode dungeon '
                     'pathfinding_enabled pathfinding_directly_to_adjacent'),
        **group(('text', r'(?:[1-9][0-9]*)?d[2368]'), 'kick_scatter_distance kick_scatter_dice throw_in_dice'),
        **group(sequence(model('Formation')), 'offensive_formations defensive_formations'),
        'time_limits': model('TimeLimits'),
    },
    'PlayerState': {
        **group(BOOL, 'up in_air used stunned bone_headed hypnotized really_stupid heated '
                     'knocked_out ejected wild_animal taken_root blood_lust picked_up '
                     'has_blocked failed_nega_trait_this_turn'),
        **group(NAT, 'spp_earned moves'), 'injuries_gained': INJURIES,
        'used_skills': sequence(SKILL, 'set rset'), 'squares_moved': SQUARES,
        **group(STRINGS, 'always_show_attr show_if_true_attr'),
    },
    'TeamState': {
        **group(NAT, 'bribes babes apothecaries score turn rerolls_start rerolls ass_coaches '
                    'cheerleaders fame time_violation'),
        **group(BOOL, 'wizard_available masterchef reroll_used'),
    },
    'GameState': {
        'stack': 'Stack', 'reports': sequence(model('Outcome')), 'half': literal(1, 2), 'round': NAT,
        **group(optional(TEAM), 'coin_toss_winner kicking_first_half receiving_first_half '
                               'kicking_this_drive receiving_this_drive current_team'),
        'teams': TEAMS, **group(TEAM, 'home_team away_team'),
        'team_by_id': mapping(TEXT, TEAM), 'player_by_id': mapping(TEXT, PLAYER),
        'team_by_player_id': mapping(TEXT, TEAM), 'pitch': model('Pitch'),
        'dugouts': mapping(TEXT, model('Dugout')), 'weather': enum('WeatherType'),
        'gentle_gust': BOOL, 'turn_order': TEAMS, 'spectators': NAT,
        'active_player': optional(PLAYER), 'game_over': BOOL, 'available_actions': CHOICES,
        'clocks': sequence('Clock'), 'rerolled_procs': sequence('procedure', 'set rset'),
        'player_action_type': optional(enum('PlayerActionType')),
    },
    'Pitch': {'balls': sequence(BALL), 'bomb': optional(model('Bomb')),
              'board': sequence(sequence(optional(PLAYER))), 'squares': sequence(SQUARES),
              'width': integer(3, 100), 'height': integer(3, 100)},
    'ActionChoice': {
        'action_type': enum('ActionType'), 'positions': sequence(optional(SQUARE)), 'players': PLAYERS,
        'team': TEAM, 'rolls': ('action-targets',),
        'block_dice': sequence(literal(-3, -2, 1, 2, 3)), 'disabled': BOOL, 'skill': optional(SKILL),
        'paths': ('empty', 'list rlist'),
    },
    'Action': {'action_type': enum('ActionType'), 'position': optional(SQUARE), 'player': optional(PLAYER)},
    'TwoPlayerArena': {
        'board': choice(sequence(sequence(enum('Tile'))), ('array', 'O', 2, enum('Tile'))), 'width': integer(3, 100), 'height': integer(3, 100), 'json': 'null',
        **group(sequence(enum('Tile')), 'home_tiles away_tiles scrimmage_tiles wing_left_tiles '
                                      'wing_right_tiles home_td_tiles away_td_tiles'),
    },
    'DiceRoll': {
        'dice': sequence(choice(model('D3'), model('D6'), model('D8'), model('BBDie'))),
        'sum': NAT, 'd68': BOOL, 'target': optional(INT), 'modifiers': INT, 'roll_type': enum('RollType'),
        **group(BOOL, 'target_higher target_lower highest_succeed lowest_fail'),
    },
    'D3': {'value': integer(1, 3)}, 'D6': {'value': integer(1, 6)}, 'D8': {'value': integer(1, 8)},
    'BBDie': {'value': enum('BBDieResult')},
    'Dugout': {'team': TEAM, **group(PLAYERS, 'reserves kod casualties dungeon')},
    'Role': {
        'name': NAME, 'races': choice(TEXT, STRINGS), **group(NAT, 'ma st ag av cost'),
        'skills': SKILLS, 'feeder': choice('null', BOOL, sequence(enum('SkillCategory'))),
        **group(sequence(enum('SkillCategory')), 'n_skill_sets d_skill_sets'), 'star_player': BOOL,
    },
    'Player': {
        'player_id': NAME, 'role': model('Role'), 'name': TEXT, 'nr': NAT, 'team': optional(TEAM),
        'extra_skills': SKILLS, **group(INT, 'extra_ma extra_st extra_ag extra_av'),
        'injuries': INJURIES, 'mng': BOOL, 'spp': NAT, 'state': model('PlayerState'),
    },
    'Square': {'x': INT, 'y': INT, '_out_of_bounds': optional(BOOL)},
    'Race': {'name': NAME, 'roles': sequence(model('Role')), 'reroll_cost': NAT,
             'apothecary': BOOL, 'stakes': BOOL},
    'Team': {'team_id': NAME, 'name': TEXT, 'race': NAME, 'players': PLAYERS,
             **group(NAT, 'treasury apothecaries rerolls fan_factor ass_coaches cheerleaders'),
             'state': model('TeamState')},
    'Outcome': {
        'outcome_type': enum('OutcomeType'), 'position': optional(SQUARE),
        **group(optional(PLAYER), 'player opp_player'), 'rolls': ROLLS, 'team': optional(TEAM),
        'n': choice('null', NUMBER, BOOL, ('enum-name', 'CasualtyEffect')), 'skill': optional(SKILL),
    },
    'Inducement': {'name': NAME, 'cost': integer(-1), 'max_num': NAT, 'reduced': choice(NAT, ('text', r'[0-9]+'))},
    'RuleSet': {
        'name': NAME, 'races': sequence(model('Race')), 'star_players': sequence(model('Role')),
        'inducements': sequence(model('Inducement')),
        **group(mapping(TEXT, NAT), 'spp_actions spp_levels improvements'), **group(NAT, 'se_start se_interval se_pace'),
    },
    'Formation': {'name': NAME, 'formation': ('matrix', '-Sspbcmavd0x')},
    'Procedure': {'game': 'Game', 'context': GRAPH, 'done': BOOL, 'started': BOOL},
    'Regeneration': {'player': PLAYER, 'regenerates': BOOL},
    'Apothecary': {
        'player': PLAYER, 'inflictor': optional(PLAYER),
        **group(BOOL, 'decay decay_roll waiting_apothecary'),
        **group(optional(ROLL), 'roll_first roll_second roll'), 'outcome': enum('OutcomeType'),
        **group(optional(enum('CasualtyType')), 'casualty_first casualty_second casualty'),
        **group(optional(enum('CasualtyEffect')), 'effect_first effect_second effect'),
        'regeneration': optional(proc('Regeneration')),
    },
    'Armor': {'player': PLAYER, 'modifiers': INT, 'inflictor': optional(PLAYER),
               **group(BOOL, 'skip_armor armor_rolled foul ejected')},
    'Stab': {'attacker': PLAYER, 'defender': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL,
             'blitz': BOOL, 'gfi': BOOL, 'foul_appearance': optional(proc('FoulAppearance'))},
    'FoulAppearance': {'attacker': PLAYER, 'defender': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL, 'revolted': BOOL},
    'Block': {
        'attacker': PLAYER, 'defender': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL,
        **group(BOOL, 'blitz gfi frenzy_block waiting_wrestle_attacker waiting_wrestle_defender '
                     'waiting_juggernaut juggernaut_checked dauntless_success frenzy_checked '
                     'waiting_dump_off waiting_foul_appearance'),
        'selected_die': optional(enum('BBDieResult')), 'favor': optional(TEAM),
        'dauntless_roll': optional(ROLL), 'foul_appearance': optional(proc('FoulAppearance')),
    },
    'Bounce': {'piece': PIECE, 'kick': BOOL},
    'Casualty': {'player': PLAYER, 'inflictor': optional(PLAYER), 'roll': optional(ROLL),
                 'casualty': optional(enum('CasualtyType')), 'effect': optional(enum('CasualtyEffect')),
                 'regeneration': optional(proc('Regeneration')),
                 **group(BOOL, 'waiting_apothecary decay decay_roll blood_lust always_hungry')},
    'Catch': {'player': PLAYER, 'piece': PIECE, **group(BOOL, 'accurate handoff kick diving'),
              'roll': optional(ROLL), 'reroll': REROLL, 'passer': optional(PLAYER), 'bomb_choice': optional(BOOL)},
    'Intercept': {'interceptor': PLAYER, 'ball': BALL, **group(optional(ROLL), 'roll safe_throw_roll'),
                  **group(REROLL, 'reroll safe_throw_reroll'), 'passer': optional(PLAYER), 'waiting_safe_throw': BOOL},
    'CoinTossKickReceive': {'aa': CHOICES},
    'Ejection': {'player': PLAYER, 'awaiting_bribe': BOOL},
    'Foul': {'fouler': PLAYER, 'defender': PLAYER},
    'Half': {'half': literal(1, 2), 'kicked_off': BOOL, 'prepared': BOOL},
    'Injury': {'player': PLAYER, 'inflictor': optional(PLAYER),
               **group(BOOL, 'injury_rolled foul mighty_blow_used dirty_player_used ejected in_crowd blood_lust stab')},
    'Interception': {'team': TEAM, 'passer': PLAYER, 'ball': BALL, 'interceptors': PLAYERS},
    'Touchback': {'ball': BALL, 'players_on_pitch_standing': PLAYERS},
    'LandKick': {'ball': BALL, 'landed': BOOL},
    'Riot': {'effect': integer(-1, 1)},
    'HighKick': {'ball': BALL, 'receiving_team': TEAM, 'available_players': PLAYERS},
    'ThrowARock': {'rolled': BOOL},
    'PitchInvasionRoll': {'team': TEAM, 'player': PLAYER},
    'KickoffTable': {'ball': BALL, 'rolled': BOOL},
    'KnockDown': {'player': PLAYER, 'inflictor': optional(PLAYER), **group(INT, 'modifiers modifiers_opp'),
                  **group(BOOL, 'armor_roll injury_roll in_crowd turnover blood_lust stab')},
    'KnockOut': {'player': PLAYER, 'inflictor': optional(PLAYER), 'roll': ROLL},
    'Leap': {'player': PLAYER, 'position': SQUARE, 'roll': optional(ROLL), 'reroll': REROLL},
    'Shadowing': {'player': PLAYER, 'position': SQUARE, 'shadowers': PLAYERS, 'shadower': optional(PLAYER),
                  'team': TEAM, 'roll': optional(ROLL), 'reroll': REROLL},
    'Tentacles': {'player': PLAYER, 'position': SQUARE, 'tentacler': PLAYER, 'move_proc': proc('Move'),
                  'roll': optional(ROLL), 'reroll': REROLL},
    'Move': {'player': PLAYER, 'position': SQUARE, 'dodge': BOOL, 'dodge_proc': optional(proc('Dodge')),
             'gfi': BOOL, 'tentaclers': PLAYERS, 'tentacles_used': BOOL},
    'GFI': {'player': PLAYER, 'position': SQUARE, 'roll': optional(ROLL), 'reroll': REROLL},
    'Dodge': {'player': PLAYER, 'position': SQUARE, 'roll': optional(ROLL), 'reroll': REROLL,
              'waiting_break_tackle': BOOL, 'break_tackle_target': optional(integer(1, 6)),
              'diving_tacklers': PLAYERS, 'diving_tackler': optional(PLAYER), 'from_position': SQUARE},
    'TurnoverIfPossessionLost': {'ball': BALL},
    'Handoff': {'ball': BALL, 'player': PLAYER, 'pos_to': SQUARE, 'catcher': PLAYER,
                'eat_thrall': optional(proc('EatThrall'))},
    'Explode': {'bomb': model('Bomb'), 'player': optional(PLAYER)},
    'Land': {'player': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL},
    'PassAttempt': {'piece': PIECE, 'passer': PLAYER, 'position': SQUARE, 'pass_distance': enum('PassDistance'),
                    'roll': optional(ROLL), 'reroll': REROLL, 'catcher': optional(PLAYER),
                    'eat_thrall': optional(proc('EatThrall')),
                    **group(BOOL, 'fumble interception_tried dump_off ttm turnover safe_throw_used')},
    'Pickup': {'ball': BALL, 'player': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL},
    'StandUp': {'player': PLAYER, 'sroll_required': BOOL, 'roll_required': BOOL, 'moves_required': NAT,
                'roll': optional(ROLL), 'reroll': REROLL},
    'PlaceBall': {'ball': BALL, 'aa': optional(CHOICES)},
    'EndPlayerTurn': {'player': PLAYER},
    'JumpUpToBlock': {'player': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL},
    'EscapeBeingEaten': {'hungry_player': PLAYER, 'delicious_player': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL},
    'AlwaysHungry': {'hungry_player': PLAYER, 'delicious_player': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL},
    'UndoPlayerAction': {'game': 'Game', 'player': PLAYER},
    'MoveAction': {'player': PLAYER, 'player_action_type': enum('PlayerActionType'), 'paths': EMPTY_PATHS,
                   'steps': optional(SQUARES), 'orig_action_type': optional(enum('ActionType')), 'can_undo': BOOL},
    'PassAction': {'dump_off': BOOL, 'picked_up_teammate': optional(PLAYER)},
    'ThrowBombAction': {'player': PLAYER, 'can_undo': BOOL},
    'BlockAction': {'player': PLAYER, 'can_undo': BOOL},
    'Frenzy': {'attacker': PLAYER, 'defender': PLAYER, 'blitz': BOOL, 'first_block': proc('Block')},
    'PreKickoff': {'team': TEAM, 'checked': PLAYERS},
    'FollowUp': {'attacker': PLAYER, 'defender': PLAYER, 'pos_to': SQUARE},
    'Push': {'pusher': PLAYER, 'player': PLAYER, 'player_chain': optional(PLAYER),
             **group(BOOL, 'knock_down blitz waiting_stand_firm stand_firm_used chain waiting_for_move '
                          'crowd strip_ball_condition'),
             **group(optional(SQUARE), 'push_to follow_to'), 'squares': optional(SQUARES), 'selector': optional(PLAYER)},
    'Scatter': {'piece': PIECE, **group(BOOL, 'kick is_pass gentle_gust')},
    'Setup': {'team': TEAM, 'reorganize': BOOL, 'selected_player': optional(PLAYER),
              'formations': sequence(model('Formation')), 'aa': CHOICES},
    'ThrowIn': {'ball': BALL, 'position': SQUARE},
    'Touchdown': {'player': PLAYER, 'handle_bloodlust': BOOL, 'eat_thrall': optional(proc('EatThrall'))},
    'TurnStunned': {'team': TEAM}, 'EndTurn': {'kickoff': BOOL},
    'Turn': {'team': TEAM, 'half': literal(1, 2), 'turn': NAT,
              **group(BOOL, 'blitz quick_snap blitz_available pass_available handoff_available foul_available')},
    'WeatherTable': {'kickoff': BOOL},
    'Negatrait': {'player': PLAYER, **group(BOOL, 'waiting_reroll reroll_used rolled ends_turn'),
                  'roll': optional(ROLL), 'reroll': REROLL, 'roll_type': optional(enum('RollType')),
                  'skill': optional(SKILL), **group(optional(enum('OutcomeType')), 'success_outcome fail_outcome')},
    'WildAnimal': {'is_block_or_blitz': BOOL},
    'BloodLustBlockOrMove': {'player': PLAYER}, 'BloodLust': {'is_block': BOOL},
    'Reroll': {'player': PLAYER, 'use_reroll': optional(BOOL), 'loner': optional(proc('Loner')),
               **group(BOOL, 'can_use_team_reroll can_use_pro secondary_clock'), 'pro': optional(proc('Pro')),
               'skill': optional(SKILL), 'block_actions': CHOICES, 'block_action': optional(model('Action'))},
    'Pro': {'success': BOOL, 'roll': optional(ROLL), 'player': PLAYER, 'reroll': REROLL},
    'Loner': {'success': BOOL, 'roll': optional(ROLL), 'player': PLAYER, 'reroll': REROLL, 'result': literal(False)},
    'EatThrall': {'player': PLAYER, 'victim': optional(PLAYER), 'failed': optional(BOOL), 'victim_pos': SQUARES},
    'HypnoticGaze': {'player': PLAYER, 'target_player': PLAYER, 'roll': optional(ROLL), 'reroll': REROLL},
    'SeedSpec': {'master_seed': integer(0, 2**256 - 1), 'episode_key': IDENTIFIER, 'component_id': IDENTIFIER,
                 'purpose': literal('scenario', 'engine', 'policy-home', 'policy-away', 'observation'),
                 'derivation_version': integer(1, 2**32 - 1)},
    'PositionV1': {'x': INT, 'y': INT},
    'SkillOptionsV1': {'skill': ('enum-name', 'Skill')},
    'PathOptionsV1': {'path': sequence(data('PositionV1'))},
    'ActionV1': {'schema_version': literal(1), 'type': ('enum-name', 'ActionType'), 'actor_id': SIDE,
                 **group(optional(('text', r'(?:home|away):[0-9]+')), 'player_id target_id'),
                 'position': optional(data('PositionV1')),
                 'options': choice(data('EmptyOptionsV1'), data('SkillOptionsV1'), data('PathOptionsV1'))},
    'TimelineContext': {'episode_id': NAME, 'branch_id': NAME, **group(NAT, 'event_seq decision_seq'),
                        **group(optional(POS), 'activation_seq team_turn_seq drive_seq'),
                        'half': optional(literal(1, 2)), 'round': optional(NAT)},
    'TimelineEvent': {'context': data('TimelineContext'), 'decision_seq': optional(NAT), 'kind': NAME,
                      'data': mapping(TEXT, JSON)},
    'DecisionEnvelope': {'before': data('TimelineContext'), 'after': data('TimelineContext'),
                         'actor_id': SIDE, 'team_id': SIDE, 'action': data('ActionV1'),
                         **group(POS, 'event_start event_stop'), 'events': sequence(data('TimelineEvent')),
                         'next_actor_id': optional(SIDE), 'terminal': BOOL, 'status': literal('resolved'),
                         'macro_id': optional(NAME), 'primitive_order': optional(NAT)},
    'TimelineCheckpoint': {'context': data('TimelineContext'), 'events': sequence(data('TimelineEvent')),
                           'decisions': sequence(data('DecisionEnvelope')), **group(sequence(JSON), 'macros operational_errors'),
                           'pending': 'null', **group(BOOL, 'activation_open turn_open drive_open')},
    'Game': {'game_id': NAME, 'config': model('Configuration'), 'arena': model('TwoPlayerArena'),
             'ruleset': model('RuleSet'), 'state': model('GameState'), 'action': optional(model('Action')),
             **group(BOOL, 'external_control _initialized _closed _end_notified'),
             **group(optional(NUMBER), 'start_time end_time last_request_time last_action_time'),
             'seed': choice('null', NAT, sequence(integer(0, 2**32 - 1))),
             **group('Agent', 'home_agent away_agent'), 'dice': 'dice', 'trajectory': 'Trajectory',
             'timeline': optional('Timeline'), 'time_source': 'LogicalTime'},
    'Clock': {'seconds': TIME, 'started_at': NUMBER, '_started_at': NUMBER, 'paused_at': optional(NUMBER),
              'paused_seconds': TIME, 'is_primary': BOOL, 'team': TEAM, 'time_source': 'LogicalTime'},
    '_ExternalAgent': {'name': TEXT, 'human': BOOL, 'agent_id': NAME},
    'Trajectory': {'enabled': BOOL}, 'Stack': {'items': sequence('procedure')},
    'ObservationControl': {'_game': 'Game', '_teams': product(TEAM, TEAM),
                           '_rosters': product(sequence(PLAYER, 'tuple'), sequence(PLAYER, 'tuple')),
                           '_team_ids': mapping(TEXT, SIDE), '_player_ids': mapping(TEXT, ('text', r'(?:home|away):[0-9]+'))},
    'LogicalTime': {'value': TIME}, 'ComponentState': {'adapter': NAME, 'state': 'component-data'},
    'Timeline': {'_game': 'Game', '_entities': 'ObservationControl', '_context': data('TimelineContext'),
                 '_events': sequence(data('TimelineEvent'), 'list'), '_decisions': sequence(data('DecisionEnvelope'), 'list'),
                 **group(sequence(JSON, 'list'), '_macros _operational_errors'),
                 '_pending': 'null', '_parent': product('null', 'null'),
                 **group(BOOL, '_activation_open _turn_open _drive_open')},
}

# Classes with no additional state still have explicit local contracts.
for _name in ('Ball Bomb CoinTossFlip ResetHalf Fans Kickoff GetTheRef CheeringFans BrilliantCoaching '
              'HandoffAction FoulAction BlitzAction StartGame EndGame Pregame ClearBoard Turnover '
              'Bonehead ReallyStupid TakeRoot EmptyOptionsV1').split():
    LOCAL[_name] = {}

# Presence rules are a closed list, separate from nullable (present with None).
# No procedure field is implicitly optional just because a constructor skips it.
LAZY = {
    'Game': {'seed': 'optional'},
    'procedure/Touchback': {'players_on_pitch_standing': 'started'},
    'procedure/HighKick': {'available_players': 'started'},
    'procedure/EatThrall': {'victim_pos': 'started'},
    'procedure/Loner': {'result': 'loner-declined'},
}

EPISODE = {
    '_seed': data('SeedSpec'), '_ids': record(**dict.fromkeys(
        ('scenario', 'engine', 'policy-home', 'policy-away', 'observation'), IDENTIFIER)),
    '_inputs': product(model('Configuration'), model('RuleSet'), model('TwoPlayerArena'), TEAM, TEAM),
    '_max_decisions': NAT, '_max_steps': POS, '_initial_teams': product(TEAM, TEAM),
    '_manifest': mapping(TEXT, JSON), '_chance_spec': TEXT, 'decisions': NAT,
    '_streams': record(**dict.fromkeys(('scenario', 'policy-home', 'policy-away', 'observation'), 'rng')),
    '_control': 'ObservationControl',
}


def contracts(classes, inventory):
    result = {}
    for tag, cls in classes.items():
        spec = {}
        for base in reversed(cls.__mro__):
            spec.update(LOCAL.get(base.__name__, {}))
        # All registered classes, including fieldless subclasses, are explicit.
        if cls.__name__ not in LOCAL:
            raise RuntimeError('Missing snapshot codec schema: ' + tag)
        if '__setattr__' in inventory[tag]:
            spec['__setattr__'] = 'null'
        if set(spec) != set(inventory[tag]):
            raise RuntimeError('Snapshot schema/inventory disagree: ' + tag + ' ' +
                               repr(sorted(set(spec) ^ set(inventory[tag]))))
        result[tag] = spec
    return result
