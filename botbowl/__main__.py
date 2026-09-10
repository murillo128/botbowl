"""Small installed-package entry points for headless and explicit web smokes."""
import argparse
from typing import Optional, Sequence


def headless_smoke(max_steps: int = 100) -> None:
    """Advance three deterministic decisions without optional integrations."""
    from botbowl import Action, ActionType, StepBudget, __version__, create_game

    game = create_game(size=3, seed=17, control="external")
    budget = StepBudget(max_steps)
    try:
        for action_type in (ActionType.START_GAME, ActionType.HEADS, ActionType.KICK):
            action = Action(action_type)
            if not game.is_action_allowed(action):
                raise RuntimeError(f"smoke action is unavailable: {action_type.name}")
            game.advance(action, max_steps=budget)
        print(f"botbowl {__version__}: headless smoke passed; next decision pending")
    finally:
        game.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m botbowl")
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    smoke = subparsers.add_parser("smoke", help="run a bounded headless engine smoke")
    smoke.add_argument("--max-steps", type=int, default=100)
    web = subparsers.add_parser("web", help="start the optional local web UI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)
    if args.version:
        from botbowl import __version__
        print(__version__)
        return 0
    if args.command == "smoke":
        if args.max_steps < 1:
            parser.error("--max-steps must be positive")
        headless_smoke(args.max_steps)
        return 0
    if args.command == "web":
        from botbowl.web.server import start_server
        # Debug and reload are intentionally never inherited from the environment.
        start_server(host=args.host, port=args.port, debug=False, use_reloader=False)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
