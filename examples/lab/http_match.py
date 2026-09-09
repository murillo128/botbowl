"""Control both teams through HTTP, from any cwd after installing botbowl.

Configure the URL and three bearer credentials in the environment. The server
must grant admin session creation/close, home play, and away play respectively.
"""
import os
from contextlib import ExitStack

from botbowl.lab.http_client import HTTPClient
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig


def main():
    url = os.environ.get("BOTBOWL_HTTP_URL", "http://127.0.0.1:5000")
    with ExitStack() as stack:
        clients = {role: stack.enter_context(HTTPClient(url, os.environ["BOTBOWL_HTTP_TOKEN_" + role.upper()]))
                   for role in ("admin", "home", "away")}
        session = clients["admin"].create(request_id=1, config=SessionConfig(size=1, max_decisions=24), seed=SeedSpec(17))
        players = {side: clients[side].attach(session.session_id) for side in ("home", "away")}
        decisions = 0
        while True:
            state = session.read()
            actions = state["legal_actions"]["actions"]
            if not actions:
                break
            action = actions[decisions % len(actions)]
            players[action["actor_id"]].step(action, state["state_revision"])
            decisions += 1
        print("decisions=%d end_reason=%s" % (decisions, state["state"]["end_reason"]))


if __name__ == "__main__":
    main()
