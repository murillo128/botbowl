"""Start the optional local web UI with debug and reload disabled."""
import argparse


def run(host: str = "127.0.0.1", port: int = 5000, check: bool = False) -> None:
    from botbowl.web.server import app, start_server

    if check:
        with app.test_client() as client:
            response = client.get("/game-modes/")
            assert response.status_code == 200
        assert not app.debug
        print("web: local application smoke passed; debug disabled")
        return
    start_server(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    run(args.host, args.port, args.check)
