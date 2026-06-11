"""Russia Relations Index worker — hourly snapshot for all 99 countries."""
import argparse
import logging
import time

from src.countries import COUNTRIES, ensure_countries_in_db
from src.db import wait_for_db
from src.engine.ru_index import calculate_ru_index, save_ru_index, warm_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ru-index")


def calc_all():
    ok, skipped = 0, 0
    for code, country in COUNTRIES.items():
        try:
            result = calculate_ru_index(code)
            if result:
                save_ru_index(result)
                ok += 1
                logger.info(
                    f"  {country['flag']} {code} {country['name_ru']}: "
                    f"{result['score']:+.1f} [{result['level']}] "
                    f"(struct {result['structural']:+.1f}, media "
                    f"{result['media'] if result['media'] is not None else '—'})"
                )
            else:
                skipped += 1
        except Exception as e:
            skipped += 1
            logger.error(f"  {code}: index error: {e}", exc_info=True)

    logger.info(f"RRI pass complete: {ok} countries, {skipped} skipped")
    warm_cache()


def main():
    parser = argparse.ArgumentParser(description="GEO PULSE Russia Relations Index")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args()

    wait_for_db()
    ensure_countries_in_db()

    if args.loop:
        logger.info(f"Starting RRI calculator (interval: {args.interval}s)")
        calc_all()
        while True:
            time.sleep(args.interval)
            try:
                calc_all()
            except Exception as e:
                logger.error(f"RRI calc error: {e}", exc_info=True)
    else:
        calc_all()


if __name__ == "__main__":
    main()
