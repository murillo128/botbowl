"""Control both teams through the authenticated SDK using caller-owned credentials."""
from contextlib import ExitStack
import os

from botbowl.examples._common import arguments, write
from botbowl.lab.http_client import HTTPClient
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig


def main():
    parser = arguments(__doc__)
    parser.add_argument('--loopback', action='store_true',
                        help='start a disposable authenticated loopback server (requires web extra)')
    args = parser.parse_args()
    names = ['BOTBOWL_HTTP_URL'] + ['BOTBOWL_HTTP_TOKEN_' + role.upper()
                                   for role in ('admin', 'home', 'away')]
    if not args.loopback and any(not os.environ.get(name) for name in names):
        parser.error('set BOTBOWL_HTTP_URL and BOTBOWL_HTTP_TOKEN_ADMIN/HOME/AWAY')
    with ExitStack() as stack:
        if args.loopback:
            from botbowl.examples._loopback import ephemeral_tokens, loopback
            tokens = ephemeral_tokens()
            # Tests/operators may supply ephemeral credentials; never print them.
            tokens.update({role: os.environ['BOTBOWL_HTTP_TOKEN_' + role.upper()]
                           for role in tokens if os.environ.get('BOTBOWL_HTTP_TOKEN_' + role.upper())})
            url = stack.enter_context(loopback(tokens))
        else:
            url = os.environ['BOTBOWL_HTTP_URL']
            tokens = {role: os.environ['BOTBOWL_HTTP_TOKEN_' + role.upper()]
                      for role in ('admin', 'home', 'away')}
        clients = {role: stack.enter_context(HTTPClient(
            url, tokens[role]))
            for role in ('admin', 'home', 'away')}
        session = clients['admin'].create(
            request_id=1, config=SessionConfig(size=1, max_decisions=args.max_decisions,
                                              max_steps=args.max_steps),
            seed=SeedSpec(args.seed, 'quickstart-http'))
        players = {side: clients[side].attach(session.session_id) for side in ('home', 'away')}
        decisions = []
        while True:
            state = session.read()
            actions = state['legal_actions']['actions']
            if not actions:
                break
            action = actions[len(decisions) % len(actions)]
            players[action['actor_id']].step(action, state['state_revision'])
            decisions.append(action)
        write(args.output / 'http.json', {'actions': decisions, 'state': state['state']})


if __name__ == '__main__':
    main()
