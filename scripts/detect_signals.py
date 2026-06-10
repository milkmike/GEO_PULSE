"""Signal intelligence worker — runs all detectors on a 30-min cadence."""
import argparse
import logging
import time

from src.db import wait_for_db
from src.engine.signals import detect_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("signals")


def main():
    parser = argparse.ArgumentParser(description="GEO PULSE signal engine")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=1800)
    args = parser.parse_args()

    wait_for_db()

    if args.loop:
        logger.info(f"Starting signal engine (interval: {args.interval}s)")
        while True:
            try:
                counts = detect_all()
                logger.info(f"Signal pass: {counts}")
            except Exception as e:
                logger.error(f"Signal pass error: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        counts = detect_all()
        logger.info(f"Signal pass: {counts}")


if __name__ == "__main__":
    main()
