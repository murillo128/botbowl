#!/usr/bin/env python3
import argparse
from scripted_bot_example import *

import botbowl.web.server as server

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the trusted local Bot Bowl web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1234)
    args = parser.parse_args()
    server.start_server(host=args.host, debug=False, use_reloader=False, port=args.port)
