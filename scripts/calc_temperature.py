"""Temperature calculation script. Runs hourly."""
import argparse
import logging
import time

from src.config import COUNTRY_NAMES
from src.db import wait_for_db
from src.engine.index import calculate_temperature, save_temperature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("temperature")


def calc_all():
    """Calculate temperature for all countries."""
    for code in COUNTRY_NAMES:
        try:
            result = calculate_temperature(code)
            if result:
                save_temperature(result)
                logger.info(f"  {code} ({COUNTRY_NAMES[code]}): {result['temperature']}°")
            else:
                logger.info(f"  {code} ({COUNTRY_NAMES[code]}): no data yet")
        except Exception as e:
            logger.error(f"  {code}: error calculating temperature: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="CIS Thermometer Calculator")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args()

    wait_for_db()

    if args.loop:
        logger.info(f"Starting temperature calculator (interval: {args.interval}s)")
        # Run immediately on start
        calc_all()
        while True:
            time.sleep(args.interval)
            try:
                calc_all()
            except Exception as e:
                logger.error(f"Temperature calc error: {e}", exc_info=True)
    else:
        calc_all()


if __name__ == "__main__":
    main()
