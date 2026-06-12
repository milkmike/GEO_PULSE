#!/usr/bin/env python3
"""Auto-load Russia-world trade data from public sources.

Strategy (per country):
1. Try UN Comtrade API (requires subscription key) — CIS countries only (numeric map)
2. Fallback: UN Comtrade public preview (free, no key) — CIS countries only (numeric map)
3. Last resort: IMF International Trade in Goods / IMTS (free, no key, ISO3 area codes) — all countries
   Formerly "Direction of Trade Statistics (DOTS)"; new portal: api.imf.org, dataflow=IMTS

Runs monthly. Upserts year rows per country: new years are inserted, existing
years are refreshed with the latest source figures (trade statistics get revised
retroactively). Source priority per pass: Comtrade (CIS) before IMF DOTS; IMF
never overwrites rows Comtrade loaded in the same pass.
Covers all 99 countries in the GEO PULSE registry (src/countries.py).
"""
import argparse
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
from src.countries import COUNTRIES, all_codes

logger = logging.getLogger(__name__)

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://thermo:thermo@localhost:5432/cis_thermometer",
)

# Russia ISO3 numeric code for Comtrade
RUSSIA_CODE = "643"

# CIS country codes: ISO3 numeric -> our ISO2.
# Comtrade branches are ONLY attempted for countries present in this map.
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

# All registry countries except Russia — the full target set
TARGET_COUNTRIES = [c for c in all_codes() if c != "RU"]

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


def get_existing_years(conn) -> set:
    """Get {(country_code, year)} set of existing records."""
    cur = conn.cursor()
    cur.execute("SELECT country_code, year FROM trade_data")
    existing = {(r[0], r[1]) for r in cur.fetchall()}
    cur.close()
    return existing


def try_comtrade_api_for_cis() -> list | None:
    """Try fetching CIS countries from UN Comtrade API (requires free subscription key).

    Only covers countries present in PARTNER_CODES (CIS numeric map).
    Returns list of flow dicts or None on failure/no key.
    """
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


def try_comtrade_bulk_for_cis() -> list | None:
    """Try UN Comtrade bulk download (public, no key needed).

    Uses the preview endpoint which has limited but free access.
    Only covers countries present in PARTNER_CODES (CIS numeric map).
    Returns list of flow dicts or None on failure.
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


def fetch_imf_dots_country(iso2: str, current_year: int) -> list:
    """Fetch IMF IMTS trade data for a single country (ISO2 = GEO PULSE registry code).

    Uses the new IMF data portal (api.imf.org), dataflow IMTS — formerly known as
    Direction of Trade Statistics (DOTS); legacy endpoint dataservices.imf.org retired.

    The new API requires ISO3 country codes (e.g. KAZ, DEU), which are looked up
    from the GEO PULSE registry (src/countries.py).  Russia reporter code is "RUS".

    Indicators (new names vs. legacy):
      XG_FOB_USD — Russia exports FOB, US dollar   (was TXG_FOB_USD)
      MG_CIF_USD — Russia imports CIF, US dollar   (was TMG_CIF_USD)

    UNITS: OBS_VALUE is raw USD (not millions). The legacy API returned millions and
    required a *1_000_000 multiplier — that multiplier is NOT applied here.

    Returns list of flow dicts (may be empty).
    Raises on network / HTTP errors so run_once() can count the country as failed.
    """
    import xml.etree.ElementTree as ET

    country_info = COUNTRIES.get(iso2.upper())
    if not country_info or not country_info.get("iso3"):
        logger.warning(f"IMF IMTS: no ISO3 code for {iso2}, skipping")
        return []

    iso3 = country_info["iso3"]

    # Dataflow: IMF.STA:IMTS(1.0.0)
    # Key order: COUNTRY.INDICATOR.COUNTERPART_COUNTRY.FREQUENCY
    # Reporter = RUS (Russia), Counterpart = iso3 (e.g. KAZ, DEU)
    # Use a 4-year lookback window: IMF IMTS data for some countries (e.g. CIS) lags
    # up to 2 years, so current_year - 2 would return 0 records by mid-year.
    url = (
        "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,IMTS,1.0.0/"
        f"RUS.XG_FOB_USD+MG_CIF_USD.{iso3}.A"
        f"?startPeriod={current_year - 4}&endPeriod={current_year}"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "GeoPulse/1.0",
        "Accept": "application/xml",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        xml_data = resp.read()

    # Parse SDMX StructureSpecificData XML.
    # The DataSet element contains interleaved Group and Series children (no namespace
    # prefix on those inner elements in this response).
    root = ET.fromstring(xml_data)
    MSG_NS = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
    dataset = root.find(f"{{{MSG_NS}}}DataSet")
    if dataset is None:
        return []

    rows = []
    for series in dataset:
        # Skip Group annotation elements; Series elements carry INDICATOR attrib
        indicator = series.attrib.get("INDICATOR", "")
        if not indicator:
            continue
        flow = "export" if indicator == "XG_FOB_USD" else "import"
        for obs in series:
            try:
                year = int(obs.attrib.get("TIME_PERIOD", 0))
                value = float(obs.attrib.get("OBS_VALUE", 0))
            except (TypeError, ValueError):
                continue
            if year > 0 and value > 0:
                rows.append(
                    {
                        "country": iso2,
                        "year": year,
                        "flow": flow,
                        "value_usd": int(value),  # API returns raw USD (not millions)
                    }
                )
    return rows


def merge_and_save(trade_records: list) -> tuple:
    """Merge export/import into per-country-year rows and save to DB.

    Returns (inserted, updated) counts.
    """
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
        return 0, 0

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
    return inserted, updated


def run_once():
    """Run one full pass across all registry countries.

    Priority per country:
      1. Comtrade API (key required) — CIS numeric map only
      2. Comtrade public preview (no key) — CIS numeric map only
      3. IMF IMTS (no key, ISO3 codes via registry) — all 99 countries

    Returns True if at least one country loaded data.
    """
    logger.info("=== Auto Trade Loader (all registry countries) ===")
    logger.info(f"Target: {len(TARGET_COUNTRIES)} countries")

    current_year = datetime.now().year
    loaded_countries: set = set()
    skipped_countries: set = set()
    failed_countries: set = set()

    # --- Step 1: Comtrade API batch (CIS countries with numeric map) ---
    comtrade_api_records = None
    if COMTRADE_API_KEY:
        comtrade_api_records = try_comtrade_api_for_cis()
        if comtrade_api_records:
            logger.info(f"Comtrade API: {len(comtrade_api_records)} flow records for CIS")

    # --- Step 2: Comtrade bulk (CIS fallback, no key) ---
    comtrade_bulk_records = None
    if not comtrade_api_records:
        comtrade_bulk_records = try_comtrade_bulk_for_cis()
        if comtrade_bulk_records:
            logger.info(f"Comtrade bulk: {len(comtrade_bulk_records)} flow records for CIS")

    # Merge whichever Comtrade source succeeded and track which CIS countries it covered
    comtrade_covered: set = set()
    comtrade_all_records = comtrade_api_records or comtrade_bulk_records or []
    if comtrade_all_records:
        inserted, updated = merge_and_save(comtrade_all_records)
        comtrade_covered = {r["country"] for r in comtrade_all_records}
        for cc in comtrade_covered:
            if cc in TARGET_COUNTRIES:
                loaded_countries.add(cc)
        logger.info(f"Comtrade saved: {inserted} new, {updated} updated")

    # --- Step 3: IMF DOTS — all remaining TARGET_COUNTRIES ---
    # For CIS countries already loaded via Comtrade, IMF DOTS is still attempted
    # as a completeness check, but Comtrade data takes priority (already saved).
    # For non-CIS countries (the majority), IMF DOTS is the only source.
    imf_records_all = []

    for iso2 in TARGET_COUNTRIES:
        # Skip if already successfully loaded via Comtrade (avoid double-write)
        if iso2 in comtrade_covered:
            continue
        try:
            rows = fetch_imf_dots_country(iso2, current_year)
            if rows:
                imf_records_all.extend(rows)
                loaded_countries.add(iso2)
            else:
                skipped_countries.add(iso2)
                logger.debug(f"IMF DOTS: no data for {iso2}")
            time.sleep(0.3)  # Be polite to the IMF API
        except Exception as e:
            failed_countries.add(iso2)
            logger.warning(f"IMF DOTS error for {iso2}: {e}")

    if imf_records_all:
        logger.info(f"IMF DOTS: {len(imf_records_all)} flow records for {len(loaded_countries - comtrade_covered)} countries")
        inserted, updated = merge_and_save(imf_records_all)
        logger.info(f"IMF DOTS saved: {inserted} new, {updated} updated")

    # --- Pass summary ---
    n_loaded = len(loaded_countries)
    n_skipped = len(skipped_countries)
    n_failed = len(failed_countries)
    logger.info(
        f"Pass complete: loaded {n_loaded} countries, "
        f"skipped {n_skipped} (no new data), "
        f"failed {n_failed}"
    )
    if failed_countries:
        logger.warning(f"Failed countries: {sorted(failed_countries)}")

    return n_loaded > 0


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
