"""FX worker — daily CBR rates into fx_rates with 1-day change."""
import argparse
import logging
import time
from datetime import date, timedelta

from sqlalchemy import text

from src.collectors.fx import fetch_cbr_rates
from src.db import get_session, wait_for_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("fx-collector")


def _prev_rate(session, currency: str, day: date) -> float | None:
    row = session.execute(
        text("""
            SELECT rate_to_rub FROM fx_rates
            WHERE currency = :cur AND day < :day
            ORDER BY day DESC LIMIT 1
        """),
        {"cur": currency, "day": day},
    ).fetchone()
    return float(row.rate_to_rub) if row else None


def collect_day(on_date: date | None = None) -> int:
    day = on_date or date.today()
    rates = fetch_cbr_rates(on_date)
    if not rates:
        logger.warning(f"No CBR rates for {day}")
        return 0

    saved = 0
    with get_session() as session:
        for currency, rate in sorted(rates.items()):
            prev = _prev_rate(session, currency, day)
            change = round((rate - prev) / prev * 100, 4) if prev else None
            session.execute(
                text("""
                    INSERT INTO fx_rates (day, currency, rate_to_rub, change_1d_pct, fetched_at)
                    VALUES (:day, :cur, :rate, :chg, NOW())
                    ON CONFLICT (day, currency) DO UPDATE SET
                        rate_to_rub = EXCLUDED.rate_to_rub,
                        change_1d_pct = COALESCE(EXCLUDED.change_1d_pct, fx_rates.change_1d_pct),
                        fetched_at = NOW()
                """),
                {"day": day, "cur": currency, "rate": rate, "chg": change},
            )
            saved += 1
    logger.info(f"FX {day}: {saved} currencies saved")
    return saved


def backfill(days: int):
    """Walk back day by day (CBR keeps full history)."""
    today = date.today()
    for offset in range(days, -1, -1):
        d = today - timedelta(days=offset)
        try:
            collect_day(d)
        except Exception as e:
            logger.error(f"FX backfill {d}: {e}")
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="GEO PULSE FX collector (CBR)")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=21600, help="default 6h")
    parser.add_argument("--backfill-days", type=int, default=0)
    args = parser.parse_args()

    wait_for_db()

    if args.backfill_days:
        backfill(args.backfill_days)
        return

    if args.loop:
        logger.info(f"Starting FX collector loop (interval: {args.interval}s)")
        while True:
            try:
                collect_day()
            except Exception as e:
                logger.error(f"FX collection error: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        collect_day()


if __name__ == "__main__":
    main()
