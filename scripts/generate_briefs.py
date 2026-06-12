"""Briefs worker — world brief every cycle + tier-1 country dossiers daily."""
import argparse
import logging
import time
from datetime import datetime, timezone

from src.countries import tier1_codes
from src.db import wait_for_db
from src.pipeline.briefs import generate_country_brief, generate_world_brief

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("briefs")


def run_pass(country_max_age_hours: float = 24.0, force: bool = False):
    generate_world_brief(force=force)
    for code in tier1_codes():
        try:
            generate_country_brief(code, max_age_hours=country_max_age_hours, force=force)
            time.sleep(1)
        except Exception as e:
            logger.error(f"Country brief {code} failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="GEO PULSE briefs generator")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=7200, help="World brief cadence, sec")
    parser.add_argument("--world-only", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if inputs are unchanged")
    args = parser.parse_args()

    wait_for_db()

    if args.loop:
        logger.info(f"Starting briefs loop (interval: {args.interval}s)")
        while True:
            try:
                if args.world_only:
                    generate_world_brief(force=args.force)
                else:
                    run_pass(force=args.force)
            except Exception as e:
                logger.error(f"Briefs pass error: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        run_pass(force=args.force)


if __name__ == "__main__":
    main()
