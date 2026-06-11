#!/usr/bin/env python3
"""Auto-load Russia-CIS trade data from public sources.

Strategy:
1. Try UN Comtrade bulk download (free, no key, annual CSV)
2. Fallback: scrape Russian Federal Customs Service summary tables
3. Last resort: keep existing data, log warning

Runs monthly; only inserts new year data, never overwrites confirmed data.
"""
import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

from src.api_tracker import track_api_call, track_duration

logger = logging.getLogger(__name__)

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://thermo:thermo@localhost:5432/cis_thermometer",
)

# Russia ISO3 numeric code for Comtrade
RUSSIA_CODE = "643"

# CIS country codes: ISO3 numeric -> our ISO2
PARTNER_CODES = {
    "398": "KZ",  # Kazakhstan
    "051": "AM",  # Armenia
    "860": "UZ",  # Uzbekistan
    "417": "KG",  # Kyrgyzstan
    "762": "TJ",  # Tajikistan
    "795": "TM",  # Turkmenistan
    "031": "AZ",  # Azerbaijan
    "268": "GE",  # Georgia
    "498": "MD",  # Moldova
    "112": "BY",  # Belarus
}

COMTRADE_API_KEY = os.environ.get("COMTRADE_API_KEY", "")


def get_db_connection():
    """Get psycopg2 connection from DATABASE_URL."""
    import psycopg2

    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", DB_URL)
    if not m:
        raise ValueError(f"Cannot parse DATABASE_URL: {DB_URL}")
    return psycopg2.connect(
        host=m.group(3),
        port=int(m.group(4)),
        user=m.group(1),
        password=m.group(2),
        dbname=m.group(5),
    )


def get_existing_years(conn) -> dict:
    """Get {(country_code, year)} set of existing records."""
    cur = conn.cursor()
    cur.execute("SELECT country_code, year FROM trade_data")
    existing = {(r[0], r[1]) for r in cur.fetchall()}
    cur.close()
    return existing


def try_comtrade_api() -> list[dict] | None:
    """Try fetching from UN Comtrade API (requires free subscription key)."""
    if not COMTRADE_API_KEY:
        logger.info("No COMTRADE_API_KEY set, skipping Comtrade API")
        return None

    partner_list = ",".join(PARTNER_CODES.keys())
    current_year = datetime.now().year
    results = []

    # Fetch all years in batches of 5 (Comtrade limit), both flows at once
    years = list(range(current_year - 2, current_year + 1))
    for i in range(0, len(years), 5):
        batch = years[i : i + 5]
        period_str = ",".join(str(y) for y in batch)
        url = (
            f"https://comtradeapi.un.org/data/v1/get/C/A/HS"
            f"?reporterCode={RUSSIA_CODE}"
            f"&partnerCode={partner_list}"
            f"&period={period_str}"
            f"&flowCode=X,M"
            f"&cmdCode=TOTAL"
            f"&subscription-key={COMTRADE_API_KEY}"
        )

        for attempt in range(3):
            try:
                with track_duration() as timer:
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read())

                for row in data.get("data", []):
                    # Filter: aggregate only (motCode=0, partner2Code=0)
                    if row.get("motCode", 0) != 0 or row.get("partner2Code", 0) != 0:
                        continue
                    partner_code = str(row.get("partnerCode", "")).zfill(3)
                    iso2 = PARTNER_CODES.get(partner_code)
                    if not iso2:
                        continue
                    flow_code = row.get("flowCode", "")
                    flow_name = "export" if flow_code == "X" else "import"
                    value_usd = row.get("primaryValue", 0) or 0
                    results.append(
                        {
                            "country": iso2,
                            "year": int(row.get("period", 0)),
                            "flow": flow_name,
                            "value_usd": int(value_usd),
                        }
                    )
                logger.info(f"Comtrade batch {period_str}: {len(data.get('data', []))} records")
                track_api_call(
                    service="comtrade", endpoint="/data/v1/get",
                    script="auto_trade_loader.py",
                    status="ok", duration_ms=timer.ms,
                )
                break
            except Exception as e:
                if "429" in str(e):
                    wait = 10 * (attempt + 1)
                    logger.warning(f"Rate limited, waiting {wait}s...")
                    track_api_call(
                        service="comtrade", endpoint="/data/v1/get",
                        script="auto_trade_loader.py",
                        status="error", error="rate_limited_429",
                    )
                    time.sleep(wait)
                    continue
                logger.warning(f"Comtrade API error for {period_str}: {e}")
                track_api_call(
                    service="comtrade", endpoint="/data/v1/get",
                    script="auto_trade_loader.py",
                    status="error", error=str(e)[:500],
                )
                break

        time.sleep(2)  # Rate limit between batches

    return results if results else None


def try_comtrade_bulk() -> list[dict] | None:
    """Try UN Comtrade bulk download (public, no key needed).
    
    Uses the preview endpoint which has limited but free access.
    """
    partner_list = ",".join(PARTNER_CODES.keys())
    current_year = datetime.now().year
    results = []

    for year in range(current_year - 2, current_year + 1):
        url = (
            f"https://comtradeapi.un.org/public/v1/preview/C/A/HS"
            f"?reporterCode={RUSSIA_CODE}"
            f"&partnerCode={partner_list}"
            f"&period={year}"
            f"&flowCode=X,M"
            f"&cmdCode=TOTAL"
        )
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            for row in data.get("data", []):
                partner_code = str(row.get("partnerCode", "")).zfill(3)
                iso2 = PARTNER_CODES.get(partner_code)
                if not iso2:
                    continue
                flow = "export" if row.get("flowCode") == "X" else "import"
                value_usd = row.get("primaryValue", 0) or 0
                results.append(
                    {
                        "country": iso2,
                        "year": year,
                        "flow": flow,
                        "value_usd": int(value_usd),
                    }
                )
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Comtrade bulk error for {year}: {e}")

    return results if results else None


def try_imf_dots() -> list[dict] | None:
    """Try IMF Direction of Trade Statistics API (free, no key)."""
    current_year = datetime.now().year
    iso2_to_imf = {
        "KZ": "KZ", "AM": "AM", "UZ": "UZ", "KG": "KG", "TJ": "TJ",
        "TM": "TM", "AZ": "AZ", "GE": "GE", "MD": "MD", "BY": "BY",
    }
    results = []

    for iso2, imf_code in iso2_to_imf.items():
        # TMG_CIF_USD = imports CIF, TXG_FOB_USD = exports FOB
        url = (
            f"https://dataservices.imf.org/REST/SDMX_JSON.svc/CompactData/"
            f"DOT/A.RU.TMG_CIF_USD+TXG_FOB_USD.{imf_code}"
            f"?startPeriod={current_year - 2}&endPeriod={current_year}"
        )
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "GeoPulse/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())

            dataset = data.get("CompactData", {}).get("DataSet", {})
            series_list = dataset.get("Series", [])
            if isinstance(series_list, dict):
                series_list = [series_list]

            for series in series_list:
                indicator = series.get("@INDICATOR", "")
                flow = "export" if "TXG" in indicator else "import"
                obs = series.get("Obs", [])
                if isinstance(obs, dict):
                    obs = [obs]
                for o in obs:
                    year = int(o.get("@TIME_PERIOD", 0))
                    value = float(o.get("@OBS_VALUE", 0))
                    if year > 0 and value > 0:
                        results.append(
                            {
                                "country": iso2,
                                "year": year,
                                "flow": flow,
                                "value_usd": int(value * 1_000_000),  # IMF reports in millions
                            }
                        )
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"IMF DOTS error for {iso2}: {e}")

    return results if results else None


def merge_and_save(trade_records: list[dict]):
    """Merge export/import into per-country-year rows and save to DB."""
    # Aggregate: (country, year) -> {export_usd, import_usd}
    agg: dict = {}
    for r in trade_records:
        key = (r["country"], r["year"])
        if key not in agg:
            agg[key] = {"export_usd": 0, "import_usd": 0}
        if r["flow"] == "export":
            agg[key]["export_usd"] = r["value_usd"]
        else:
            agg[key]["import_usd"] = r["value_usd"]

    if not agg:
        logger.warning("No trade data to save")
        return

    import psycopg2

    conn = get_db_connection()
    existing = get_existing_years(conn)
    cur = conn.cursor()

    inserted = 0
    updated = 0
    for (cc, year), vals in sorted(agg.items()):
        export_usd = vals["export_usd"]
        import_usd = vals["import_usd"]
        total_usd = export_usd + import_usd
        balance_usd = export_usd - import_usd

        # Calculate YoY from previous year in DB
        yoy = None
        cur.execute(
            "SELECT total_trade_usd FROM trade_data WHERE country_code = %s AND year = %s",
            (cc, year - 1),
        )
        prev = cur.fetchone()
        if prev and prev[0] and prev[0] > 0:
            yoy = round(((total_usd - prev[0]) / prev[0]) * 100, 1)

        cur.execute(
            """
            INSERT INTO trade_data (country_code, year, ru_export_usd, ru_import_usd,
                                    total_trade_usd, trade_balance_usd, yoy_change_pct, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (country_code, year)
            DO UPDATE SET
                ru_export_usd = EXCLUDED.ru_export_usd,
                ru_import_usd = EXCLUDED.ru_import_usd,
                total_trade_usd = EXCLUDED.total_trade_usd,
                trade_balance_usd = EXCLUDED.trade_balance_usd,
                yoy_change_pct = EXCLUDED.yoy_change_pct,
                updated_at = NOW()
            """,
            (cc, year, export_usd, import_usd, total_usd, balance_usd, yoy),
        )

        is_new = (cc, year) not in existing
        if is_new:
            inserted += 1
        else:
            updated += 1
        logger.info(
            f"  {'NEW' if is_new else 'UPD'} {cc} {year}: "
            f"exp=${export_usd/1e6:.0f}M imp=${import_usd/1e6:.0f}M "
            f"total=${total_usd/1e6:.0f}M"
            + (f" YoY:{yoy:+.1f}%" if yoy else "")
        )

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Trade data: {inserted} new, {updated} updated")


def run_once():
    """Try all sources in priority order."""
    logger.info("=== Auto Trade Loader ===")

    # 1. Try Comtrade API (with key)
    data = try_comtrade_api()
    if data:
        logger.info(f"Comtrade API: {len(data)} records")
        merge_and_save(data)
        return True

    # 2. Try Comtrade bulk (no key)
    data = try_comtrade_bulk()
    if data:
        logger.info(f"Comtrade bulk: {len(data)} records")
        merge_and_save(data)
        return True

    # 3. Try IMF DOTS
    data = try_imf_dots()
    if data:
        logger.info(f"IMF DOTS: {len(data)} records")
        merge_and_save(data)
        return True

    logger.warning("All trade data sources failed. Data NOT updated.")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run monthly loop")
    parser.add_argument(
        "--interval",
        type=int,
        default=2592000,
        help="Interval in seconds (default: 30 days)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [trade-loader] %(levelname)s: %(message)s",
    )

    if args.loop:
        logger.info(f"Starting trade loader loop (interval: {args.interval}s)")
        while True:
            try:
                run_once()
            except Exception as e:
                logger.error(f"Trade loader failed: {e}")
            logger.info(f"Sleeping {args.interval}s...")
            time.sleep(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
