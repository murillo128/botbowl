"""CPU-only research demo: python -m examples.lab.research_viewer /tmp/research-demo.

Use an empty destination. Choose a token at the prompt, then enter it in the
browser. The demo grants explicit evaluator snapshot/restore capability.
"""
import argparse
import getpass
import json
from pathlib import Path
import secrets

import botbowl as bb
from botbowl.lab.commands import Access, CommandGateway, SessionRegistry
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.replays import ReplayRecorder
from botbowl.lab.research import ResearchStore, pack_replay
from botbowl.web.research import create_app


def build_demo(destination):
    """Return an owned store plus an uploadable factual bundle and example record."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    config = bb.load_config('gym-1')
    game = bb.create_game(config, size=1, seed=51, control='external')
    recorder = ReplayRecorder(game, destination, 'factual', replay_id='demo-factual',
                              origin_family_id='research-demo', checkpoint_interval=2)
    store = ResearchStore({'demo': Access('evaluator', capabilities=frozenset({'snapshot', 'restore'}))})
    try:
        for _ in range(3):
            recorder.advance(recorder.actions.legal_actions().actions[0])
        recorder.close(truncation_reason='demo_prefix')
        bundle = pack_replay(destination / 'factual')
        (destination / 'factual-upload.json').write_text(json.dumps(bundle), encoding='utf-8')
        row = store.upload('demo', bundle)
        context = store.frame(row['id'], 1)['context']
        prediction = {'prediction': {
            'schema_version': 1, 'kind': 'predicted', 'prediction_id': 'artificial-forecast',
            'origin_family_id': 'research-demo', 'branch_id': context['branch_id'],
            'parent_snapshot_id': 'demo-decision-1', 'model': {'model_id': 'artificial-constant', 'version': 'v1'},
            'issued_at': context, 'available_history': {'through': context, 'references': []},
            'horizon': 2, 'output': {'home_score': 0, 'illustrative_interval': [0, 1]},
            'metadata': {'note': 'Artificial predictor: constant output; not trained or calibrated.'},
            'revision_of': None}, 'target_decision': 1, 'retrospective': False}
        store.prediction(row['id'], prediction)
        (destination / 'prediction.json').write_text(json.dumps(prediction, indent=2) + '\n', encoding='utf-8')
        for i, action in enumerate(store.actions('demo', row['id'], 1)[:2]):
            store.fork('demo', row['id'], {'decision': 1, 'action': action, 'horizon': i + 1},
                       seed=SeedSpec(51, 'research-demo', component_id=str(i)))
        return store
    except Exception:
        store.close()
        raise
    finally:
        game.close()


def main():
    from werkzeug.serving import make_server
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    parser.add_argument('--port', type=int, default=5001)
    args = parser.parse_args()
    token = getpass.getpass('Choose a local research access token: ')
    if not token:
        parser.error('A nonempty token is required')
    store = build_demo(args.destination)
    registry = SessionRegistry()
    gateway = CommandGateway(registry, lambda value: 'demo' if isinstance(value, str) and
                              secrets.compare_digest(value, token) else None)
    server = make_server('127.0.0.1', args.port, create_app(gateway, store), threaded=True)
    print('Open http://127.0.0.1:%d/research/ and enter the token you chose.' % server.server_port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()
        registry.close()


if __name__ == '__main__':
    main()
