"""Real Chromium research navigation; run explicitly with the web test extra."""
import json
from threading import Thread

import pytest

playwright = pytest.importorskip('playwright.sync_api')
pytest.importorskip('flask')
from werkzeug.serving import make_server
from tests.lab.test_research_viewer import research, auth, prediction  # noqa: F401

expect = playwright.expect
expect.set_options(timeout=60000)


@pytest.fixture
def browser():
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(research, browser):
    app, store, bundle, manifest = research
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto('http://127.0.0.1:%d/research/' % server.server_port)
        yield page, store, bundle
        assert not errors
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def connect(page, role='viewer'):
    page.get_by_label('Access token').fill(role)
    page.get_by_role('button', name='Connect / reconnect').click()
    expect(page.get_by_role('status')).to_have_text('Connected · navigation is read-only')


def open_replay(page, bundle):
    page.get_by_label('Local ReplayV1 bundle').set_input_files({
        'name': 'local.json', 'mimeType': 'application/json', 'buffer': json.dumps(bundle).encode()})
    expect(page.locator('.panel')).to_have_count(1)


def go(page, decision):
    page.get_by_label('Decision', exact=True).fill(str(decision))
    page.get_by_role('button', name='Go to decision', exact=True).click()
    expect(page.locator('.context').first).to_contain_text('"decision_seq": ' + str(decision))


def test_replay_search_branch_sync_values_reconnect_and_labels(page):
    page, store, bundle = page
    connect(page)
    open_replay(page, bundle)
    expect(page.get_by_role('button', name='Load legal alternatives')).to_be_disabled()
    go(page, 1)
    player = page.locator('[data-entity-id="home:0"]')
    player.focus()
    player.press('Enter')
    expect(page.locator('.entity-detail')).to_contain_text('"moves": 0')
    expect(page.locator('.entity-detail')).to_contain_text('Missing data (off pitch)')
    metrics = page.locator('canvas').evaluate('(c) => ({width:c.width,height:c.height,display:c.getBoundingClientRect().width})')
    assert metrics['width'] == 6 * 12 and metrics['height'] == 5 * 12
    assert metrics['display'] > 0
    page.get_by_label('Event type').fill('report')
    page.get_by_role('button', name='Search events').click()
    expect(page.locator('#event option').nth(1)).to_be_attached()
    value = page.locator('#event option').nth(1).get_attribute('value')
    page.get_by_label('Event', exact=True).select_option(value)
    expect(page.locator('#boundary')).to_contain_text('preceding restorable decision')
    expect(page.get_by_role('button', name='Load legal alternatives')).to_be_disabled()
    connect(page, 'editor')
    expect(page.get_by_role('button', name='Load legal alternatives')).to_be_disabled()
    go(page, 1)
    for horizon, action in ((1, '0'), (2, '1')):
        page.get_by_role('button', name='Load legal alternatives').click()
        expect(page.get_by_role('button', name='Create branch', exact=True)).to_be_enabled()
        page.get_by_label('Initial action').select_option(action)
        page.get_by_label('Decision horizon').fill(str(horizon))
        page.get_by_role('button', name='Create branch', exact=True).click()
        expect(page.locator('.panel')).to_have_count(horizon + 1)
    go(page, 0)
    expect(page.locator('.alignment').nth(1)).to_have_text('Shared factual prefix')
    assert all(json.loads(v)['decision_seq'] == 0 for v in page.locator('.context').all_text_contents())
    go(page, 3)
    contexts = [json.loads(v) for v in page.locator('.context').all_text_contents()]
    assert [v['decision_seq'] for v in contexts] == [3, 2, 3]
    assert len({v['branch_id'] for v in contexts}) == 3
    expect(page.locator('.exhausted')).to_contain_text('No data at requested decision 3')
    expect(page.locator('.alignment').nth(2)).to_contain_text('Horizon: 2')
    expect(page.locator('.panel h2').nth(1)).to_contain_text('Simulated continuation')
    before = {key: store.summary(key) for key in store.entries}
    page.get_by_role('button', name='Play navigation').click()
    page.get_by_role('button', name='Pause navigation').click()
    connect(page, 'viewer')
    expect(page.locator('.panel')).to_have_count(3)
    assert before == {key: store.summary(key) for key in store.entries}
    root = next(row for row in store.entries if store.summary(row)['provenance']['kind'] == 'observed')
    context = store.frame(root, 1)['context']
    data = {'prediction': prediction(store.summary(root), context), 'target_decision': 1, 'retrospective': False}
    page.get_by_label('Prediction JSON').fill(json.dumps(data))
    page.get_by_role('button', name='Import prediction').click()
    expect(page.get_by_role('heading', name='Model prediction', exact=True)).to_be_visible()
    expect(page.locator('#records')).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('#records img').count() == 0
    text = '<script>window.researchInjected=true</script>'
    page.get_by_label('Human annotation', exact=True).fill(text)
    page.get_by_role('button', name='Add annotation').click()
    expect(page.get_by_role('heading', name='Human annotation', exact=True)).to_be_visible()
    assert page.evaluate('window.researchInjected') is None
    assert store.summary(root)['predictions'][0]['record']['output']['probability'] == 0


def test_invalid_file_missing_resource_and_keyboard_navigation(page):
    page, store, bundle = page
    connect(page)
    page.get_by_label('Local ReplayV1 bundle').set_input_files({
        'name': 'broken.json', 'mimeType': 'application/json', 'buffer': b'{"format":'})
    expect(page.get_by_role('status')).not_to_have_text('Connected · navigation is read-only')
    assert not store.entries
    open_replay(page, bundle)
    page.get_by_label('Turn', exact=True).select_option(index=1)
    expect(page.locator('.context')).to_contain_text('"decision_seq": 1')
    button = page.get_by_role('button', name='Next decision', exact=True)
    button.focus()
    assert button.evaluate('e=>getComputedStyle(e).outlineStyle') != 'none'
    button.press('Enter')
    expect(page.locator('.context')).to_contain_text('"decision_seq": 2')
    assert page.request.get(page.url + 'replays/missing/frame?decision=0', headers=auth()).status == 404
