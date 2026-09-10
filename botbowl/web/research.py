"""Explicit opt-in loopback research page, separate from competitive web routes."""
from pathlib import Path
import threading

from flask import Blueprint, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from botbowl.lab.commands import CapacityExceeded, GatewayError, InvalidRequest, _parse, GatewayLimits
from botbowl.lab.http import create_app as create_gateway_app, _loopback, ERROR_STATUS
from botbowl.lab.replays import ReplayError


def create_app(gateway, store):
    app = create_gateway_app(gateway)
    bp = Blueprint('research', __name__, url_prefix='/research')
    assets = Path(__file__).parent
    slots = threading.BoundedSemaphore(4)

    @bp.before_request
    def guard():
        # Validate Host as well as peer to reject DNS rebinding. Browser writes
        # need same-origin access; the SDK's no-Origin API remains unchanged.
        host = request.host.split(':')[0]
        if app.debug or not _loopback(request.remote_addr or '') or host not in ('127.0.0.1', 'localhost'):
            return jsonify(error='forbidden'), 403
        origin = request.headers.get('Origin')
        if origin is not None and origin != request.host_url.rstrip('/'):
            return jsonify(error='forbidden'), 403
        if request.path in ('/research/', '/research/viewer.js', '/research/viewer.css'):
            return
        auth = request.headers.get('Authorization', '')
        token = auth[7:] if auth.startswith('Bearer ') and len(auth) <= 8192 else None
        g.principal = gateway._principal(token)
        store.access(g.principal)
        if not slots.acquire(blocking=False):
            raise CapacityExceeded()
        g.research_slot = True
        request.max_content_length = store.limits.max_bytes

    @bp.teardown_request
    def release(error):
        if g.pop('research_slot', False):
            slots.release()

    @bp.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Referrer-Policy': 'no-referrer'})
        return response

    @bp.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, GatewayError):
            return jsonify(error=exc.code), ERROR_STATUS.get(exc.code, 400)
        if isinstance(exc, KeyError):
            return jsonify(error='not_found'), 404
        if isinstance(exc, HTTPException):
            return jsonify(error='too_large' if exc.code == 413 else 'invalid_request'), exc.code
        if isinstance(exc, (ValueError, TypeError, ReplayError)):
            return jsonify(error='invalid_replay_or_data'), 400
        return jsonify(error='internal_error'), 500

    def body():
        if request.mimetype != 'application/json':
            raise InvalidRequest()
        return _parse(request.get_data(cache=False), GatewayLimits(max_request_bytes=store.limits.max_bytes))

    def integer(name):
        raw = request.args.get(name)
        if raw is None or not raw.isdecimal() or len(raw) > 10:
            raise InvalidRequest()
        return int(raw)

    @bp.get('/')
    def index():
        return send_file(assets / 'templates/research.html')

    @bp.get('/viewer.js')
    def javascript():
        return send_file(assets / 'static/dist/js/research.js')

    @bp.get('/viewer.css')
    def stylesheet():
        return send_file(assets / 'static/dist/css/research.css')

    @bp.route('/replays', methods=['GET', 'POST'])
    def replays():
        with store.lock:
            if request.method == 'POST':
                return jsonify(store.upload(g.principal, body())), 201
            grant = store.access(g.principal)
            return jsonify(replays=[store.summary(key, g.principal) for key in store.entries],
                           can_fork=grant.role == 'evaluator' and {'snapshot', 'restore'} <= grant.capabilities,
                           max_bytes=store.limits.max_bytes, max_horizon=store.limits.max_decisions)

    @bp.get('/replays/<identity>/<resource>')
    def read(identity, resource):
        with store.lock:
            if resource == 'frame':
                return jsonify(store.frame(identity, integer('decision'), request.args.get('entity')))
            if resource == 'events':
                return jsonify(events=store.events(identity, request.args.get('kind', ''), request.args.get('entity', '')))
            if resource == 'event':
                return jsonify(store.event(identity, integer('event')))
            if resource == 'actions':
                return jsonify(actions=store.actions(g.principal, identity, integer('decision')))
            if resource == 'annotation-bundles':
                return jsonify(bundles=store.annotation_bundles(identity, g.principal))
            return jsonify(error='not_found'), 404

    @bp.post('/replays/<identity>/<resource>')
    def write(identity, resource):
        with store.lock:
            # Authorization precedes decoding for executable operations.
            if resource == 'branches':
                store.access(g.principal, fork=True)
                return jsonify(store.fork(g.principal, identity, body())), 201
            if resource == 'predictions':
                return jsonify(store.prediction(identity, body())), 201
            if resource == 'annotations':
                return jsonify(store.annotate(identity, body())), 201
            if resource == 'annotation-bundles':
                return jsonify(store.import_annotations(g.principal, identity, body())), 201
            if resource == 'export':
                return jsonify(store.export_fragment(g.principal, identity, body()))
            return jsonify(error='not_found'), 404

    app.register_blueprint(bp)
    return app
