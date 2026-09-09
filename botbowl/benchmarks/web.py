"""Opt-in localhost HTTP and real Angular/Chromium rendering measurements.

Requires the web extra and separately installed Playwright/Chromium. No remote
service, transport change, asset copy, or browser dependency in the core suite.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from threading import Thread
import time
from unittest.mock import patch

from .headless import environment, fixture, state_json, summary, digest
from .prototypes import measure


@contextmanager
def local_server(game, temp_root=None):
    from botbowl.web import api
    from botbowl.web.host import InMemoryHost
    from botbowl.web.server import app
    from werkzeug.serving import make_server, WSGIRequestHandler

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix='botbowl-web-benchmark-', dir=temp_root) as directory:
        host = InMemoryHost(Path(directory) / 'saves', Path(directory) / 'replays')
        host.add_game(game)
        with patch.object(api, 'host', host):
            server = make_server('127.0.0.1', 0, app, request_handler=QuietHandler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield app, 'http://127.0.0.1:{}'.format(server.server_port)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


RENDER = """async ({frames, repetitions, warmup}) => {
    const scope = angular.element(document.querySelector('[ng-view] > *')).scope();
    const result = {parse_ms: [], update_digest_layout_ms: [], next_frame_ms: [], cells: []};
    // Disable scheduled refresh/clock work during isolated render measurements.
    scope.checkForReload = () => {};
    scope.runTimeLoop = () => {};
    scope.reload = () => {}; // Also neutralize a refresh timer already scheduled.
    for (let run = -warmup; run < repetitions; run++) {
        let parse = 0, update = 0, frameWait = 0;
        for (const text of frames) {
            let start = performance.now();
            const data = JSON.parse(text);
            parse += performance.now() - start;
            start = performance.now();
            scope.$apply(() => {
                scope.game = data;
                scope.disableOppActions();
                scope.playersById = Object.assign({}, data.state.home_team.players_by_id, data.state.away_team.players_by_id);
                scope.setLocalState();
                scope.setAvailablePositions();
                scope.loading = false;
                scope.refreshing = false;
            });
            document.body.getBoundingClientRect(); // Force layout, not a GPU-paint claim.
            update += performance.now() - start;
            start = performance.now();
            await new Promise(resolve => requestAnimationFrame(resolve));
            frameWait += performance.now() - start;
        }
        if (run >= 0) {
            result.parse_ms.push(parse);
            result.update_digest_layout_ms.push(update);
            result.next_frame_ms.push(frameWait);
            result.cells.push(document.querySelectorAll('.pitch-square').length);
        }
    }
    return result;
}"""


def benchmark_web(frames, browser_executable, repetitions=5, warmup=1, poll_seconds=5.6):
    if not frames or repetitions < 2 or warmup < 0 or poll_seconds <= 0:
        raise ValueError('Nonempty frames, >=2 repetitions, warmup >=0 and positive polling window required')
    from playwright.sync_api import sync_playwright
    game = fixture(7)
    before = state_json(game)
    report = dict(schema_version=1, environment=environment(), repetitions=repetitions,
                  warmup=warmup, frame_count=len(frames), corpus_sha256=digest(frames),
                  polling_window_seconds=poll_seconds, browser_errors=[], failed_requests=[])
    with local_server(game) as (app, url):
        client = app.test_client()

        def poll(method):
            response = client.get('/games/' + game.game_id) if method == 'GET' else client.post(
                '/games/' + game.game_id + '/update', json={})
            assert response.status_code == 200
            assert response.get_json()['game_id'] == game.game_id
            return len(response.data)

        report['http'] = {}
        snapshot, conversion = measure(game.to_json, repetitions, warmup=warmup)
        _, serialization = measure(lambda: app.json.dumps(snapshot), repetitions, warmup=warmup)
        report['http']['state_to_json'] = conversion
        report['http']['flask_json_encode'] = serialization
        for method in ['GET', 'POST']:
            size, measurement = measure(lambda: poll(method), repetitions, warmup=warmup)
            report['http'][method] = dict(response_bytes=size, **measurement)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(browser_executable), headless=True)
            try:
                report['browser'] = browser.version
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: report['browser_errors'].append(str(error)))
                page.on('requestfailed', lambda request: report['failed_requests'].append(request.url))
                # Only serve the existing local application. Never fetch remote assets.
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(url + '/')
                           else route.abort())
                requests = []
                page.on('request', lambda request: requests.append(dict(
                    method=request.method, url=request.url, time=time.perf_counter()))
                        if '/games/' in request.url else None)
                page.goto(url + '/#/game/spectate/' + game.game_id)
                page.wait_for_function("document.querySelectorAll('.pitch-square').length > 0")
                poll_start = time.perf_counter()
                page.wait_for_timeout(poll_seconds * 1000)
                report['observed_poll_requests'] = [dict(method=r['method'], path=r['url'].replace(url, ''),
                                                        seconds=r['time'] - poll_start) for r in requests]
                report['browser_http_resources'] = page.evaluate("""() => performance.getEntriesByType('resource')
                    .filter(r => r.name.includes('/games/')).map(r => ({
                        path: new URL(r.name).pathname, duration_ms: r.duration,
                        transfer_bytes: r.transferSize, encoded_bytes: r.encodedBodySize,
                        decoded_bytes: r.decodedBodySize}))""")
                session = page.context.new_cdp_session(page)
                session.send('Performance.enable')
                metrics_before = {m['name']: m['value'] for m in session.send('Performance.getMetrics')['metrics']}
                raw = page.evaluate(RENDER, dict(frames=[json.dumps(f) for f in frames],
                                                repetitions=repetitions, warmup=warmup))
                metrics_after = {m['name']: m['value'] for m in session.send('Performance.getMetrics')['metrics']}
                report['render'] = {key: summary(values) for key, values in raw.items() if key != 'cells'}
                report['render']['cells'] = raw['cells']
                report['render']['cdp_seconds_including_warmup'] = {
                    key: metrics_after[key] - metrics_before[key]
                    for key in ['LayoutDuration', 'RecalcStyleDuration', 'ScriptDuration']}
                report['render']['js_heap_used_bytes'] = metrics_after['JSHeapUsedSize']
                assert all(c == game.arena.width * game.arena.height for c in raw['cells'])
                assert not report['browser_errors'], report['browser_errors']
            finally:
                browser.close()
        assert state_json(game) == before, 'Idle polling unexpectedly advanced a human decision'
    report['semantic_identity'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=Path, required=True)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--warmup', type=int, default=1)
    args = parser.parse_args()
    report = benchmark_web(json.loads(args.frames.read_text()), args.browser,
                           args.repetitions, args.warmup)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
