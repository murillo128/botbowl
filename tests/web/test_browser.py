"""Real Chromium/Angular integration against an isolated local Flask test server.

Run explicitly with pytest tests/web/test_browser.py. Requires the optional
Playwright test tool and its Chromium browser, never a runtime dependency.
All clock control and synthetic fixtures live in this test process; there are
no test routes in the application. Artwork requests are suppressed.
"""
from threading import Thread
from types import SimpleNamespace
import pickle
import re

import pytest

playwright = pytest.importorskip('playwright.sync_api')
pytest.importorskip('flask')
from werkzeug.serving import make_server
import botbowl as bb
from botbowl.web import api, server
from botbowl.web.host import InMemoryHost
from tests.framework.test_forced_action import FakeTime
from tests.util import get_custom_game_turn

expect = playwright.expect
SCOPE = "angular.element(document.querySelector('[ng-view]')).scope()"


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def web(tmp_path, monkeypatch, browser):
    host = InMemoryHost(tmp_path / 'saves', tmp_path / 'replays')
    now = FakeTime()
    original = api.Game

    def make_game(*args, **kwargs):
        return original(*args, **kwargs, time_source=now, seed=1)

    monkeypatch.setattr(api, 'host', host)
    monkeypatch.setattr(api, 'Game', make_game)
    http = make_server('127.0.0.1', 0, server.app, threaded=True)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page.set_default_timeout(7000)
    page.route('**/static/img/**', lambda route: route.fulfill(status=204))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text)
            if message.type == 'error' and re.search(r'TypeError|ReferenceError|Error: \[', message.text) else None)
    try:
        yield SimpleNamespace(page=page, host=host, now=now, base=f'http://127.0.0.1:{http.server_port}')
        assert not errors
    finally:
        page.close()
        http.shutdown()
        thread.join(timeout=5)
        http.server_close()


def observe(web, game_id):
    web.page.goto(f'{web.base}/#/game/hotseat/{game_id}')
    expect(web.page.locator('.gameboard')).to_be_visible()


def reload_game(page):
    with page.expect_response(lambda response: '/games/' in response.url and response.request.method == 'GET'):
        page.evaluate(f'{SCOPE}.reload()')
    page.wait_for_function(f'!{SCOPE}.refreshing')


def create(web, mode='1v1', local=True):
    page = web.page
    page.goto(f'{web.base}/#/game/create/{mode}')
    page.wait_for_selector('#homeTeam option[ng-repeat]', state='attached')
    name = page.locator('#homeTeam option[ng-repeat]').first.get_attribute('value')
    page.locator('#homeTeam').select_option(name)
    page.locator('#awayTeam').select_option(name)
    if local:
        page.get_by_role('checkbox', name='Local practice').check()
    with page.expect_response('**/game/create') as response:
        page.get_by_role('button', name='Create Game', exact=True).click()
    assert response.value.status == 200
    data = response.value.json()
    observe(web, data['game_id'])
    return web.host.get_game(data['game_id'])


@pytest.mark.parametrize('mode,dimensions', [('1v1', (6, 5)), ('3v3', (14, 7)),
                                         ('5v5', (18, 11)), ('7v7', (22, 11)),
                                         ('standard', (28, 17))])
@pytest.mark.parametrize('value', [9, 10, 11])
def test_attribute_dom_selection_and_board_sizes(web, mode, dimensions, value):
    page = web.page
    # This is a component fixture: only the browser's observation copy gets
    # synthetic attributes/placements, never the engine or on-disk rosters.
    name = api.get_teams(mode)[0].name
    response = page.request.put(web.base + '/game/create', data={
        'mode': mode, 'game': {'home_team_name': name, 'away_team_name': name, 'local': True}})
    game_id = response.json()['game_id']
    observe(web, game_id)
    page.evaluate(f'''value => {{
        const s = {SCOPE};
        const state = s.game.state;
        for (const [side,x] of [['home',2],['away',3]]) {{
            const player = Object.values(state[side+'_team'].players_by_id)[0];
            ['ma','st','ag','av'].forEach(key => player[key] = value);
            player.name = side + ' synthetic player';
            player.position = {{x,y:2}};
            state.pitch.board[2][x] = player.player_id;
            state[side+'_dugout'].reserves = Object.keys(state[side+'_team'].players_by_id).filter(id=>id!==player.player_id);
        }}
        s.setLocalState();s.setAvailablePositions();s.$apply();
    }}''', value)
    assert page.evaluate(f'[ {SCOPE}.local_state.board[0].length, {SCOPE}.local_state.board.length ]') == list(dimensions)
    expect(page.locator('.pitch-square')).to_have_count(dimensions[0] * dimensions[1])
    for side in ('home', 'away'):
        name = page.evaluate(f'Object.values({SCOPE}.game.state.{side}_team.players_by_id)[0].name')
        square = page.get_by_role('button', name=f'{name}, number', exact=False)
        if side == 'home':
            square.click()
        else:
            square.focus()
            square.press('Enter')
        expect(square).to_have_attribute('aria-pressed', 'true')
        expect(square).to_have_class(re.compile(r'.*\bselected\b.*'))
        expect(square).to_have_attribute('aria-label', re.compile(f'MA {value}, ST {value}, AG {value}, AV {value}'))
        panel = page.locator(f'.{side}-info player-attributes')
        expect(panel.locator('dd')).to_have_text([str(value)] * 4)
        expect(panel.locator('dd').nth(1)).to_have_attribute('aria-label', f'Strength: {value}')
        for scale in (1, 2):
            page.evaluate('scale => document.body.style.fontSize = (1.3*scale)+"em"', scale)
            metrics = panel.evaluate('''e => {
                const panel = e.getBoundingClientRect();
                return Array.from(e.querySelectorAll('dd')).map(dd => {
                    const r = document.createRange(); r.selectNodeContents(dd);
                    const t = r.getBoundingClientRect(), box = dd.getBoundingClientRect();
                    return {fits: t.left >= box.left && t.right <= box.right &&
                        t.top >= box.top && t.bottom <= box.bottom,
                        inside: box.left >= panel.left && box.right <= panel.right,
                        left: t.left, right: t.right};
                });
            }''')
            assert all(m['fits'] and m['inside'] for m in metrics), metrics
            assert all(a['right'] < b['left'] for a, b in zip(metrics, metrics[1:])), metrics
        page.evaluate('document.body.style.fontSize = "1.3em"')
    # Existing dugout selection remains keyboard reachable as well.
    dugout = page.locator('.dugout.home .square[tabindex="0"]').first
    dugout.focus()
    dugout.press(' ')
    expect(dugout).to_have_attribute('aria-pressed', 'true')


def test_create_pause_fake_time_rejection_resume_and_transition(web):
    page = web.page
    game = create(web)
    prefix = web.base + '/games/' + game.game_id
    page.get_by_role('button', name='Start game', exact=True).click()
    # The existing slow driver exposes automatic frames before CoinToss.
    for _ in range(10):
        if game.state.clocks and game.state.available_actions:
            break
        assert page.request.post(prefix + '/update', data={}).status == 200
    reload_game(page)
    assert game.state.clocks
    web.now.now = 2
    reload_game(page)
    page.keyboard.press('Tab')
    page.get_by_role('button', name='Pause game', exact=True).focus()
    assert page.get_by_role('button', name='Pause game', exact=True).evaluate('e => getComputedStyle(e).outlineStyle') != 'none'
    page.get_by_role('button', name='Pause game', exact=True).press('Enter')
    expect(page.get_by_role('button', name='Resume game')).to_have_attribute('aria-pressed', 'true')
    expect(page.get_by_role('status').filter(has_text='Game paused')).to_be_visible()
    elapsed = [clock.get_running_time() for clock in game.state.clocks]
    web.now.now += 1000
    frozen = pickle.dumps(game)
    for _ in range(2):
        assert page.request.post(prefix + '/pause', data={}).json()['paused']
        assert page.request.post(prefix + '/update', data={}).json()['paused']
        rejected = page.request.post(prefix + '/act', data={'action': {'action_type': 'HEADS'}})
        assert rejected.status == 409 and rejected.json()['error']['code'] == 'game_paused'
        assert pickle.dumps(game) == frozen
    # Browser time can advance too: paused duration/driver stay frozen and the
    # local UI cannot emit an action or automatic update while paused.
    page.clock.install()
    requests = []
    page.on('request', lambda request: requests.append(request.url) if request.method == 'POST' else None)
    displayed = page.locator('.clock').first.inner_text()
    page.clock.fast_forward(3000)
    expect(page.locator('.clock').first).to_have_text(displayed)
    assert not requests
    expect(page.get_by_role('button', name='Heads', exact=True)).to_be_disabled()
    assert [clock.get_running_time() for clock in game.state.clocks] == elapsed
    page.get_by_role('button', name='Resume game', exact=True).click()
    expect(page.get_by_role('button', name='Pause game')).to_have_attribute('aria-pressed', 'false')
    assert [clock.get_running_time() for clock in game.state.clocks] == elapsed
    resumed = pickle.dumps(game)
    assert page.request.post(prefix + '/resume', data={}).status == 200
    assert pickle.dumps(game) == resumed
    web.now.now += 1
    assert [clock.get_running_time() for clock in game.state.clocks] == [t + 1 for t in elapsed]
    before = game.state.to_json(ignore_clocks=True)
    page.get_by_role('button', name='Heads', exact=True).click()
    page.wait_for_function(f'!{SCOPE}.refreshing')
    assert game.state.to_json(ignore_clocks=True) != before
    assert not game.state.game_over
    assert not page.get_by_role('alert').count()


def test_competition_cannot_pause_or_resume(web):
    game = create(web, local=False)
    assert game.config.competition_mode
    expect(web.page.get_by_role('button', name='Pause game')).to_have_count(0)
    expect(web.page.get_by_role('status').filter(has_text='Competition game')).to_be_visible()
    before = pickle.dumps(game)
    for operation in ('pause', 'resume', 'pause', 'resume'):
        response = web.page.request.post(f'{web.base}/games/{game.game_id}/{operation}', data={})
        assert response.status == 409 and response.json()['error']['code'] == 'pause_not_allowed'
        assert pickle.dumps(game) == before


def test_stale_action_http_error_visible_without_success(web):
    page = web.page
    game = create(web)
    # A second local client pauses while this page still shows a legal action.
    assert page.request.post(f'{web.base}/games/{game.game_id}/pause', data={}).status == 200
    frozen = pickle.dumps(game)
    with page.expect_response('**/act') as response:
        page.get_by_role('button', name='Start game', exact=True).click()
    assert response.value.status == 409
    expect(page.get_by_role('alert')).to_have_text('Game is paused. Resume before playing.')
    expect(page.get_by_role('button', name='Resume game')).to_be_visible()
    assert pickle.dumps(game) == frozen
    expect(page.get_by_role('button', name='Start game', exact=True)).to_be_disabled()


def test_create_http_failure_visible(web):
    page = web.page
    page.goto(web.base + '/#/game/create/1v1')
    page.wait_for_selector('#homeTeam option[ng-repeat]', state='attached')
    page.evaluate(f'''() => {{ const s={SCOPE};s.home_team_name='missing';s.away_team_name='missing';s.$apply(); }}''')
    with page.expect_response('**/game/create') as response:
        page.get_by_role('button', name='Create Game', exact=True).click()
    assert response.value.status == 400
    expect(page.get_by_role('alert')).to_have_text('Unknown team for this game mode.')
    assert not web.host.games
    assert '/game/create/1v1' in page.url


def test_touchdown_event_refresh_reload_and_replay_do_not_duplicate_or_score(web):
    page = web.page
    game, players = get_custom_game_turn([(5, 8)], [(6, 8)], ball_position=(2, 2))
    game.game_id = 'touchdown-fixture'
    game.config.competition_mode = False
    game.time_source = web.now
    scorer = next(player for player in players if player.team == game.active_team)
    endzone = 1 if scorer.team == game.state.home_team else game.arena.width - 2
    game.move(scorer, game.get_square(endzone + (1 if endzone == 1 else -1), 8))
    game.get_ball().move_to(scorer.position)
    game.get_ball().is_carried = True
    game.set_available_actions()
    game.step(bb.Action(bb.ActionType.START_MOVE, player=scorer))
    replay = bb.Replay('notice-fixture')
    replay.record_step(game)
    web.host.add_game(game)
    observe(web, game.game_id)
    page.clock.install()
    notice = page.locator('.touchdown-notice')
    expect(notice).to_have_text('')
    initial_score = scorer.team.state.score
    # Click the real legal movement target. Touchdown and score come from the
    # engine's normal MOVE -> Touchdown -> drive transition, not injected JSON.
    target = page.locator('#squares > .square-row').nth(8).locator('.pitch-square').nth(endzone)
    target.click()
    expect(notice).to_have_text('Touchdown — ' + scorer.team.name + '!')
    assert scorer.team.state.score == initial_score + 1
    assert sum(report.outcome_type == bb.OutcomeType.TOUCHDOWN for report in game.state.reports) == 1
    replay.record_step(game)
    replay_path = web.host.replay_dir / 'notice-fixture.rep'
    replay_path.parent.mkdir()
    replay.reports = list(game.state.reports)
    replay_path.write_bytes(pickle.dumps(replay))
    score = (game.state.home_team.state.score, game.state.away_team.state.score)
    expect(notice).to_have_attribute('aria-live', 'polite')
    assert notice.evaluate('e => getComputedStyle(e).pointerEvents') == 'none'
    assert notice.evaluate('e => getComputedStyle(e).animationName') == 'none'
    assert page.evaluate('matchMedia("(prefers-reduced-motion: reduce)").matches')
    # Notice never owns focus or disables the next real decision.
    expect(page.locator('.btn-act:enabled').first).to_be_enabled()
    page.clock.fast_forward(2500)
    reload_game(page)
    page.clock.fast_forward(1600)
    expect(notice).to_have_text('')  # repeated report did not restart the timer
    reload_game(page)
    expect(notice).to_have_text('')
    assert (game.state.home_team.state.score, game.state.away_team.state.score) == score
    page.reload()
    expect(page.locator('.gameboard')).to_be_visible()
    expect(page.locator('.touchdown-notice')).to_have_text('')
    page.goto(web.base + '/#/game/replay/notice-fixture/')
    expect(page.get_by_role('button', name='Next replay frame')).to_be_enabled()
    for _ in range(2):
        page.get_by_role('button', name='Next replay frame').click()
        expect(page.locator('.touchdown-notice')).to_have_text('')
        page.get_by_role('button', name='Previous replay frame').click()
        expect(page.locator('.touchdown-notice')).to_have_text('')
    assert (game.state.home_team.state.score, game.state.away_team.state.score) == score


def test_internal_http_failure_keeps_decision_visible(web, monkeypatch):
    page = web.page
    game = create(web)
    state = game.state.to_json()
    rng = game.capture_rng_state()

    def fail(*args, **kwargs):
        raise RuntimeError('synthetic engine failure')

    monkeypatch.setattr(game, 'step', fail)
    with page.expect_response('**/act') as response:
        page.get_by_role('button', name='Start game', exact=True).click()
    assert response.value.status == 500
    expect(page.get_by_role('alert')).to_have_text('Internal server error.')
    expect(page.get_by_role('button', name='Start game', exact=True)).to_be_enabled()
    assert game.state.to_json() == state and game.capture_rng_state() == rng
    assert page.evaluate(f'{SCOPE}.saved') is False


def test_missing_replay_error_stays_visible(web):
    web.page.goto(web.base + '/#/game/replay/missing/')
    expect(web.page.get_by_role('alert')).to_have_text('Replay not found.')
    assert '/game/replay/missing/' in web.page.url


def test_failed_save_shows_server_message_in_dialog(web):
    page = web.page
    create(web)
    page.get_by_text('Save', exact=True).first.click()
    page.get_by_label('Name', exact=True).fill('../invalid')
    with page.expect_response('**/game/save') as response:
        page.get_by_role('button', name='Save', exact=True).click()
    assert response.value.status == 400
    expect(page.get_by_role('alert')).to_contain_text('Save names must be')
    expect(page.get_by_role('dialog').first).to_be_visible()
    assert page.evaluate(f'{SCOPE}.saved') is False
