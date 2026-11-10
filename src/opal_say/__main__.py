"""Run opal-say-svc: `opal-say-svc [--config PATH] [--host H] [--port P]`."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from opal_say.config import load_config
from opal_say.service.lifecycle import wait_for_pids, write_pid_file


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="opal-say-svc", description="OPAL Say TTS REST service")
    parser.add_argument("--config", help="service YAML config (default: $OPAL_SAY_CONFIG or ./config/service.yaml)")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    logging.basicConfig(level=config.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Managed-server handshake (service lifecycle): a replacement child waits for the
    # pids it was launched against to exit before binding, then records its real pid.
    wait = os.environ.get("OPAL_SAY_WAIT_FOR_PID")
    if wait:
        wait_for_pids(wait.split(","))
    if pid_file := os.environ.get("OPAL_SAY_PID_FILE"):
        write_pid_file(Path(pid_file))

    from opal_say.api.app import create_app

    uvicorn.run(create_app(config), host=config.host, port=config.port, log_level=config.log_level.lower())


if __name__ == "__main__":
    main()
