"""Real Chromium research navigation; run explicitly with the web test extra."""
import json
from copy import deepcopy
from threading import Thread

import pytest

playwright = pytest.importorskip('playwright.sync_api')
pytest.importorskip('flask')
from werkzeug.serving import make_server
from botbowl.lab.research import pack_replay
from tests.lab.test_replays import record
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
    expect(page.locator('.alignment').nth(2)).to_contain_text('Requested horizon: 2')
    expect(page.locator('.panel h2').nth(1)).to_contain_text('Simulated continuation')
    tops = page.locator('canvas').evaluate_all('(canvases)=>canvases.map(c=>c.getBoundingClientRect().top)')
    assert max(tops) - min(tops) < 2
    expect(page.locator('.entity-detail').nth(1)).to_contain_text('Simulated values')
    page.locator('.panel details').nth(1).locator('summary').click()
    expect(page.locator('.panel .provenance').nth(1)).to_be_visible()
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
    original_b = page.locator('#branch-b').input_value()
    page.locator('#branch-b').select_option(page.locator('#branch-a').input_value())
    expect(page.get_by_role('status')).to_have_text('Choose distinct alternatives from the same divergence decision')
    expect(page.locator('.panel')).to_have_count(0)
    action = store.actions('editor', root, 2)[0]
    different = store.fork('editor', root, {'decision': 2, 'action': action, 'horizon': 1})
    page.locator('#branch-b').select_option(original_b)
    expect(page.locator('.panel')).to_have_count(3)
    connect(page)
    page.locator('#branch-b').select_option(different['id'])
    expect(page.get_by_role('status')).to_have_text('Choose distinct alternatives from the same divergence decision')
    expect(page.locator('.panel')).to_have_count(0)


def test_invalid_file_missing_resource_and_keyboard_navigation(page):
    page, store, bundle = page
    connect(page)
    page.get_by_label('Local ReplayV1 bundle').set_input_files({
        'name': 'broken.json', 'mimeType': 'application/json', 'buffer': b'{"format":'})
    expect(page.get_by_role('status')).not_to_have_text('Connected · navigation is read-only')
    assert not store.entries
    truncated = deepcopy(bundle)
    truncated['files']['manifest.json'] = truncated['files']['manifest.json'][:-4]
    page.get_by_label('Local ReplayV1 bundle').set_input_files({
        'name': 'truncated.json', 'mimeType': 'application/json', 'buffer': json.dumps(truncated).encode()})
    expect(page.get_by_role('status')).to_have_text('invalid_replay_or_data')
    assert not store.entries
    open_replay(page, bundle)
    page.get_by_label('Turn', exact=True).select_option(index=1)
    expect(page.locator('.context')).to_contain_text('"decision_seq": 1')
    button = page.get_by_role('button', name='Next decision', exact=True)
    page.keyboard.press('Tab')
    button.focus()
    assert button.evaluate('e=>getComputedStyle(e).outlineStyle') != 'none'
    button.press('Enter')
    expect(page.locator('.context')).to_contain_text('"decision_seq": 2')
    assert page.request.get(page.url + 'replays/missing/frame?decision=0', headers=auth()).status == 404


@pytest.mark.parametrize('pending', ['event', 'search', 'frame'])
@pytest.mark.parametrize('destination', ['replay', 'decision'])
def test_navigation_discards_delayed_event_work(page, tmp_path, pending, destination):
    page, store, bundle = page
    game, _, _, _, _ = record(tmp_path, decisions=0, name='empty')
    try:
        empty = store.upload('viewer', pack_replay(tmp_path / 'empty'))
    finally:
        game.close()
    assert empty['final']['event_seq'] == 0
    connect(page, 'editor')
    open_replay(page, bundle)
    root = page.locator('#factual').input_value()
    page.get_by_role('button', name='Search events').click()
    expect(page.locator('#event option').nth(1)).to_be_attached()
    event = page.locator('#event option').nth(1).get_attribute('value')
    suffix = {'event': '/event?event=' + event, 'search': '/events?*', 'frame': '/frame?*'}[pending]
    held = []

    def hold_response(route):
        if held:
            route.continue_()
            return
        held.append((route, route.fetch()))
        page.locator('body').evaluate("body => body.dataset.responseHeld = 'yes'")

    page.route('**/replays/' + root + suffix, hold_response)
    if pending == 'search':
        page.get_by_role('button', name='Search events').click()
    else:
        page.get_by_label('Event', exact=True).select_option(event)
    expect(page.locator('body')).to_have_attribute('data-response-held', 'yes')
    assert len(held) == 1
    # Only the captured response remains delayed; navigation uses the real server.
    if destination == 'replay':
        page.locator('#factual').select_option(empty['id'])
        expect(page.locator('.panel')).to_have_attribute('data-replay-id', empty['id'])
        target = 0
    else:
        go(page, 3)
        target = 3
    before = page.locator('.context').text_content()
    route, response = held[0]
    with page.expect_response(response.url) as released:
        route.fulfill(response=response)
    released.value.finished()
    # Let fetch consumers run before waiting for any frame requests they start.
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    page.wait_for_load_state('networkidle')
    expect(page.locator('.context')).to_have_text(before)
    expect(page.locator('#decision')).to_have_value(str(target))
    expect(page.locator('#boundary')).to_be_empty()
    expect(page.locator('#event-detail')).to_be_empty()
    expect(page.get_by_role('button', name='Load legal alternatives')).to_be_enabled()
    expect(page.get_by_role('status')).to_have_text('Connected · navigation is read-only')
    if destination == 'replay':
        expect(page.locator('#event option')).to_have_count(1)


def test_external_layers_entity_join_text_toggle_and_download(page):
    from tests.lab.test_annotations import annotation_bundle
    page, store, bundle = page
    connect(page)
    open_replay(page, bundle)
    root = page.locator('#factual').input_value()
    go(page, 1)
    frame = store.frame(root, 1)
    data = annotation_bundle(frame['context'], kind='projection_2d')
    note = annotation_bundle(frame['context'], kind='human_note')['items'][0]
    note['annotation_id'] = 'html-note'
    data['items'].append(note)
    before = store.summary(root)['predictions']
    page.get_by_label('Annotation / export replay').select_option(root)
    page.get_by_label('AnnotationBundleV1 JSON file').set_input_files({
        'name': 'annotations.json', 'mimeType': 'application/json', 'buffer': json.dumps(data).encode()})
    toggle = page.get_by_label('External layer · artificial', exact=True)
    expect(toggle).to_be_checked()
    expect(page.locator('[data-annotation-entity="home:0"]')).to_contain_text('[\n  1,\n  4\n]')
    expect(page.locator('[data-annotation-entity="away:0"]')).to_contain_text('[\n  2,\n  3\n]')
    expect(page.locator('.external-layer-content')).to_contain_text('<script>window.annotationInjected=true</script>')
    assert page.locator('.external-layer-content script').count() == 0
    assert page.evaluate('window.annotationInjected') is None
    toggle.uncheck()
    expect(page.locator('.external-layer-content')).to_be_hidden()
    toggle.check()
    expect(page.locator('.external-layer-content')).to_be_visible()
    assert store.frame(root, 1) == frame
    assert store.summary(root)['predictions'] == before
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Export current decision with synthetic image').click()
    assert downloaded.value.suggested_filename == 'research-export.json'
    go(page, 2)
    expect(page.locator('.external-layer-content')).to_have_count(0)


def test_branch_initial_annotations_keep_shared_board_and_branch_isolation(page):
    from tests.lab.test_annotations import annotation_bundle
    page, store, bundle = page
    connect(page)
    open_replay(page, bundle)
    root = page.locator('#factual').input_value()
    actions = store.actions('editor', root, 1)
    branches = [store.fork('editor', root, {'decision': 1, 'action': action, 'horizon': 1})
                for action in actions[:2]]
    branch = branches[0]
    frame = store.frame(branch['id'], 1)
    data = annotation_bundle(frame['context'])
    connect(page)
    page.locator('#branch-a').select_option(branch['id'])
    expect(page.locator('.panel')).to_have_count(2)
    page.locator('#branch-b').select_option(branches[1]['id'])
    expect(page.locator('.panel')).to_have_count(3)
    go(page, 1)
    page.get_by_label('Annotation / export replay').select_option(branch['id'])
    page.get_by_label('AnnotationBundleV1 JSON file').set_input_files({
        'name': 'branch.json', 'mimeType': 'application/json', 'buffer': json.dumps(data).encode()})
    panel = page.locator('.panel[data-replay-id="' + branch['id'] + '"]')
    toggle = panel.get_by_label('External layer · artificial', exact=True)
    expect(toggle).to_be_checked()
    assert store.annotation_bundles(branch['id']) == [data]
    expect(panel.locator('.alignment')).to_have_text('Shared factual prefix')
    # The displayed board/context still belongs to the factual prefix.
    factual_context = store.frame(root, 1)['context']
    assert json.loads(panel.locator('.context').text_content()) == factual_context
    canvases = page.locator('.panel > canvas').evaluate_all('(cs)=>cs.map(c=>c.toDataURL())')
    assert len(set(canvases)) == 1
    expect(panel.locator('[data-annotation-entity="home:0"]')).to_contain_text('10')
    for other in (root, branches[1]['id']):
        expect(page.locator('.panel[data-replay-id="' + other + '"] .external-layer-content')).to_have_count(0)
    toggle.uncheck()
    expect(panel.locator('.external-layer-content')).to_be_hidden()
    toggle.check()
    expect(panel.locator('.external-layer-content')).to_be_visible()
    assert store.frame(branch['id'], 1) == frame
    for decision in (0, 2):
        go(page, decision)
        expect(page.locator('.external-layer-content')).to_have_count(0)
    go(page, 1)
    expect(toggle).to_be_checked()
