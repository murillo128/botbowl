/* Run with gjs tests/web/frontend_contract.js from the repository root.
 * Exercise the shipped Angular controller/services with HTTP and DOM adapters.
 * No browser, network, npm installation or artwork is needed.
 */
const GLib = imports.gi.GLib;
const ByteArray = imports.byteArray;
function read(path) { return ByteArray.toString(GLib.file_get_contents(path)[1]); }
function check(value, message) { if (!value) throw new Error(message); }
const sourceNames = ['app', 'controllers', 'directives', 'filters', 'services'];
const source = sourceNames.map(name => read('botbowl/web/static/js/' + name + '.js')).join('\n');
const bundle = read('botbowl/web/static/dist/js/botbowl.js');
check(bundle === source, 'Shipped bundle must equal source concatenation');

for (const [label, code] of [['source', source], ['bundle', bundle]]) {
    const factories = {}, controllers = {}, calls = [], timers = [];
    const module = {
        factory(name, fn) { factories[name] = fn; },
        controller(name, spec) { controllers[name] = spec[spec.length - 1]; },
        directive() {}, filter() {}, config() {}
    };
    const window = {location: {protocol: 'http:', host: 'localhost', href: '/game/hotseat/test'}};
    const document = {addEventListener() {}, getElementById() { return {scrollTop: 0}; }};
    const jquery = () => ({ready() {}, width() {}});
    const pending = [];
    const http = {};
    for (const method of ['get', 'post', 'put', 'delete']) {
        http[method] = (url, data) => {
            calls.push({method, url, data});
            const response = pending.shift();
            const promise = {
                success(fn) { if (response && !response.error) fn(response.data); return promise; },
                error(fn) { if (response && response.error) fn(response.data, response.status); return promise; }
            };
            return promise;
        };
    }
    Function('angular', 'window', 'document', '$', 'setTimeout', 'console', code)(
        {module() { return module; }}, window, document, jquery,
        fn => { timers.push(fn); }, {log() {}});
    const gameService = factories.GameService(http), replayService = factories.ReplayService(http);
    gameService.get('test');
    gameService.update('test');
    gameService.load('Round One');
    check(calls[0].method === 'get' && calls[0].url.endsWith('/games/test'), 'Observation is GET');
    check(calls[1].method === 'post' && calls[1].url.endsWith('/games/test/update'), 'Update is explicit POST');
    check(calls[2].method === 'post' && calls[2].url.endsWith('/game/load/Round%20One'), 'Load is POST with encoded name');
    replayService.getSteps('Replay One', 100, 10);
    check(calls[3].url.endsWith('/steps/Replay%20One/100/10'), 'Replay paging uses encoded IDs');

    const scope = {$apply(fn) { if (fn) fn(); }};
    controllers.GamePlayCtrl(scope, {id: 'test'}, {path() { throw new Error('Unexpected navigation'); }}, {},
        gameService, {}, {log_timouts: {}}, replayService, {});
    const data = {game_id: 'test', state: {game_over: false, reports: [], home_team: {players_by_id: {}}, away_team: {players_by_id: {}}}};
    // Rendering is outside this harness; keep interaction, timers and errors real.
    scope.disableOppActions = scope.setLocalState = scope.setAvailablePositions = scope.setClock = () => {};
    scope.getActiveClock = () => null;
    scope.reload();
    check(calls[calls.length - 1].method === 'get', 'Initial load observes');
    pending.push({data});
    scope.reload(true);
    check(calls[calls.length - 1].url.endsWith('/update'), 'Polling advances via update');
    const timerCount = timers.length;
    pending.push({data});
    scope.reload(true);
    check(timers.length === timerCount + 1, 'Reload schedules observation poll but no second clock loop');

    scope.loading = false;
    scope.refreshing = false;
    scope.opp_turn = true;
    scope.checkForReload(10);
    const poll = timers.pop();
    poll();
    check(calls[calls.length - 1].url.endsWith('/update'), 'Opponent polling must not send CONTINUE action');

    scope.refreshing = false;
    scope.getActiveClock = () => ({});
    scope.getSecondsLeft = () => -1;
    scope.runTimeLoop(20, 'test');
    const clockTick = timers.pop();
    clockTick();
    check(calls[calls.length - 1].url.endsWith('/update'), 'Expired clock uses explicit update');

    scope.refreshing = false;
    pending.push({error: true, status: 409, data: {error: {message: 'Decision changed.'}}});
    pending.push({data});
    scope.act({action_type: 'START_MOVE'});
    check(scope.error === 'Decision changed.' && !scope.refreshing, 'Rejected action displays error and releases UI');
    check(calls[calls.length - 1].method === 'get' && calls[calls.length - 1].url.endsWith('/games/test'), 'Conflict recovery observes without update');

    scope.refreshing = false;
    pending.push({error: true, status: 500, data: {error: {message: 'Internal server error.'}}});
    const count = calls.length;
    scope.act({action_type: 'START_MOVE'});
    check(scope.error === 'Internal server error.' && !scope.refreshing, 'Internal action failure is visible');
    check(calls.length === count + 1, 'Internal failure is not retried as an action');

    scope.replaying = true;
    scope.replay_id = 'empty';
    pending.push({data: {steps: {}, actions: {}}});
    scope.reload();
    check(scope.emptyReplay && !scope.loading && !scope.refreshing, 'Empty replay renders without dereferencing frame zero');
    print(label + ': HTTP verbs, encoding, polling, timer ownership, conflict recovery, internal failure, empty replay PASS');
}
