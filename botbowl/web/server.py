"""
==========================
Author: Niels Justesen
Year: 2018
==========================
Run this script to start a Flask server locally. The server will start a Host, which will manage games.
"""
from flask import Flask, g, request, render_template, jsonify
from werkzeug.exceptions import HTTPException
from botbowl.web import api
from botbowl.web.errors import WebError
from botbowl.core.model import Action, Agent, Square
from botbowl.core.table import ActionType
from botbowl.ai.registry import make_bot

app = Flask(__name__)


@app.before_request
def lock_host():
    # Hold through validation, execution and serialization so two local clients
    # observe whole transitions. This is a single-process in-memory host.
    g.game_host = api.host
    g.game_host.lock.acquire()


@app.teardown_request
def unlock_host(error):
    host = g.pop('game_host', None)
    if host is not None:
        host.lock.release()


@app.errorhandler(WebError)
def web_error(error):
    return jsonify(error={'code': error.code, 'message': str(error)}), error.status


@app.errorhandler(HTTPException)
def http_error(error):
    response = error.get_response()
    response.data = app.json.dumps({'error': {'code': error.name.lower().replace(' ', '_'),
                                             'message': error.description}})
    response.content_type = 'application/json'
    return response


@app.errorhandler(Exception)
def internal_error(error):
    app.logger.error("Local web request failed", exc_info=True)
    return jsonify(error={'code': 'internal_error', 'message': 'Internal server error.'}), 500


def body():
    data = request.get_json(force=True)  # Old local clients omit Content-Type.
    if not isinstance(data, dict):
        raise WebError("Expected a JSON object.")
    return data


def text_field(data, key, default=None):
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise WebError("Expected a nonempty string for " + key + ".")
    return value


def empty_body():
    if request.get_data() and body():
        raise WebError("This operation takes no parameters.")


@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')


@app.route('/game/create', methods=['PUT'])
def create():
    data = body()
    settings = data.get('game')
    if not isinstance(settings, dict):
        raise WebError("Expected game settings.")
    mode = data.get('mode', 'standard')
    api.get_config(mode)
    agents = []
    for key in ('home_player', 'away_player'):
        name = text_field(settings, key, 'human')
        if name == 'human':
            agents.append(Agent(f"Player {len(agents) + 1}", human=True))
        elif name in api.get_bots():
            agents.append(make_bot(name))
        else:
            raise WebError("Unknown coach.")
    game = api.new_game(home_team_name=text_field(settings, 'home_team_name'),
                        away_team_name=text_field(settings, 'away_team_name'),
                        home_agent=agents[0], away_agent=agents[1], game_mode=mode,
                        local=settings.get('local', False))
    return jsonify(api.game_to_json(game))


@app.route('/game/save', methods=['POST'])
def save():
    data = body()
    api.save_game(text_field(data, 'game_id'), text_field(data, 'name'))
    return jsonify("Game was successfully saved")


@app.route('/game/<game_id>/delete', methods=['DELETE'])
def delete(game_id):
    api.delete_game(game_id)
    return jsonify(f"{game_id} deleted")


@app.route('/save/<name>/delete', methods=['DELETE'])
def delete_saved(name):
    api.delete_save(name)
    return jsonify(f"{name} deleted")


@app.route('/games/', methods=['GET'])
def get_all_games():
    return jsonify(games=[api.game_to_json(game) for game in api.get_games()],
                   saved_games=[{'name': name, 'game': api.game_to_json(game)} for name, game in api.get_saved_games()])


@app.route('/game-modes/', methods=['GET'])
def get_all_game_modes():
    return jsonify(list(api.game_modes))


@app.route('/replays/', methods=['GET'])
def get_all_replays():
    return jsonify(replays=api.get_replay_ids())


@app.route('/teams/<game_mode>', methods=['GET'])
def get_all_teams(game_mode):
    return jsonify([team.to_json() for team in api.get_teams(game_mode)])


@app.route('/games/<game_id>/act', methods=['POST'])
def step(game_id):
    data = body()
    if 'action' not in data:
        raise WebError("An action field is required.")
    game = api.get_game(game_id)
    api.host.require_running(game)
    raw = data['action']
    action = None
    if raw is not None:
        if not isinstance(raw, dict):
            raise WebError("Action must be an object or null.")
        name = text_field(raw, 'action_type')
        try:
            action_type = ActionType[name]
        except KeyError:
            raise WebError("Unknown action type.") from None
        position = raw.get('position')
        if position is not None:
            if not isinstance(position, dict) or any(type(position.get(key)) is not int for key in ('x', 'y')):
                raise WebError("Position requires integer x and y coordinates.")
            position = Square(position['x'], position['y'])
        player_id = raw.get('player_id')
        player = None
        if player_id is not None:
            if not isinstance(player_id, str):
                raise WebError("Player ID must be a string.")
            player = game.state.player_by_id.get(player_id)
            if player is None:
                raise WebError("Player ID does not belong to this game.", 409, 'unknown_player')
        action = Action(action_type, position=position, player=player)
    # Only initial client validation is a rejection. The request lock keeps
    # these choices current until step; later bot/engine failures are internal
    # errors and may follow accepted execution that already changed the game.
    validation = game.validate_action(action)
    if not validation.allowed:
        raise WebError(validation.message, 409, validation.code)
    return jsonify(api.game_to_json(api.step(game_id, action)))


@app.route('/games/<game_id>/update', methods=['POST'])
def update_game(game_id):
    empty_body()
    return jsonify(api.game_to_json(api.update_game(game_id)))


@app.route('/games/<game_id>/pause', methods=['POST'])
def pause_game(game_id):
    empty_body()
    return jsonify(api.game_to_json(api.host.pause_game(game_id)))


@app.route('/games/<game_id>/resume', methods=['POST'])
def resume_game(game_id):
    empty_body()
    return jsonify(api.game_to_json(api.host.resume_game(game_id)))


@app.route('/games/<game_id>', methods=['GET'])
def get_game(game_id):
    return jsonify(api.game_to_json(api.get_game(game_id)))


@app.route('/replays/<replay_id>', methods=['GET'])
def get_replay(replay_id):
    return jsonify(api.get_replay(replay_id).to_json())


@app.route('/steps/<replay_id>/<from_idx>/<num_steps>', methods=['GET'])
def get_steps(replay_id, from_idx, num_steps):
    try:
        offset, count = int(from_idx), int(num_steps)
    except ValueError:
        raise WebError("Replay offset and page size must be integers.") from None
    steps = api.get_replay_steps(replay_id, offset, count)
    return jsonify({idx: step.game for idx, step in steps.items()})


@app.route('/game/load/<name>', methods=['POST'], strict_slashes=False)
def load_game(name):
    empty_body()
    return jsonify(api.game_to_json(api.load_game(name)))


@app.route('/bots/', methods=['GET'])
def get_bots():
    return jsonify(api.get_bots())


def start_server(debug=False, use_reloader=False, port=5000, host="127.0.0.1"):
    # Change jinja notation to work with angularjs
    jinja_options = app.jinja_options.copy()
    jinja_options.update(dict(
        block_start_string='<%', block_end_string='%>',
        variable_start_string='%%', variable_end_string='%%',
        comment_start_string='<#', comment_end_string='#>'
    ))
    app.jinja_options = jinja_options
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(host=host, debug=debug, use_reloader=use_reloader, port=port)
