from __future__ import annotations

import argparse
from pathlib import Path

from backend.config import ServiceConfig
from backend.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Zotero annotation local sync service")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.json"),
        help="Path to service config JSON",
    )
    args = parser.parse_args()
    run_server(ServiceConfig.load(args.config.resolve()))


if __name__ == "__main__":
    main()

