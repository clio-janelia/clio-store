#!/usr/bin/env python3
"""Local dev server launcher.

Loads .env into the environment so config.py picks up DSG_URL etc.
Without --certs: runs uvicorn with auto-reload (HTTP).
With --certs <dir>: runs hypercorn with auto-reload, access logging to stdout,
and TLS using <dir>/localhost+2.pem and <dir>/localhost+2-key.pem.

Usage:
    pixi run dev
    pixi run dev --certs /path/to/cert-dir
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _envutil import load_env


def main():
    parser = argparse.ArgumentParser(description="Run clio-store locally")
    parser.add_argument(
        "--certs",
        metavar="DIR",
        help="Directory containing localhost+2.pem and localhost+2-key.pem; "
             "when set, runs hypercorn with TLS + HTTP/2",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default="8080")
    args = parser.parse_args()

    # Load .env into the process environment (existing env vars win).
    for key, value in load_env().items():
        os.environ.setdefault(key, value)

    if args.certs:
        certfile = os.path.join(args.certs, "localhost+2.pem")
        keyfile = os.path.join(args.certs, "localhost+2-key.pem")
        for path in (certfile, keyfile):
            if not os.path.isfile(path):
                sys.exit(f"error: {path} not found")
        cmd = [
            "hypercorn", "main:app",
            "--bind", f"{args.host}:{args.port}",
            "--certfile", certfile,
            "--keyfile", keyfile,
            "--access-logfile", "-",
            "--reload",
        ]
    else:
        cmd = [
            "uvicorn", "main:app",
            "--reload",
            "--host", args.host,
            "--port", args.port,
        ]

    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
