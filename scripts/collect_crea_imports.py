#!/usr/bin/env python3
"""Russian fossil-fuel imports per destination country — CREA Russia Fossil Tracker.

Source (free, no key): https://api.russiafossiltracker.com/v0/counter

We pull the cumulative value (EUR) and volume (tonnes) of Russian fossil-fuel
exports since the 2022 full-scale invasion, aggregated by destination country and
commodity group, then upsert one row per importer into `ru_fossil_imports` with a
per-commodity breakdown and a world rank by euros paid to Russia.

This is the "who keeps funding Russia via energy" layer (worldmonitor energy-radar
analog) — a direct dependence/leverage signal for the country↔Russia lens.

Run:
    python scripts/collect_crea_imports.py            # one-shot
    python scripts/collect_crea_imports.py --loop     # daily refresh
"""
import argparse
import json
import logging
import os
import time
import urllib.parse
import urllib.request

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:thermo@localhost:5432/cis_thermometer")
# Cumulative since the full-scale invasion — CREA's "Financing Putin's war" window.
PERIOD_FROM = os.environ.get("CREA_PERIOD_FROM", "2022-02-24")
API_URL = "https://api.russiafossiltracker.com/v0/counter"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [crea] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def fetch_counter() -> list[dict]:
    """Cumulative Russian fossil exports by destination country + commodity group."""
    params = urllib.parse.urlencode({
        "aggregate_by": "destination_country,commodity_group",
        "date_from": PERIOD_FROM,
        "format": "json",
    })
    url = f"{API_URL}?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "GeoPulse/1.0 (research; https://github.com/milkmike/GEO_PULSE)",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    # API may wrap rows in {"data": [...]} or return a bare list.
    rows = data.get("data", data) if isinstance(data, dict) else data
    logger.info("Fetched %d (country×commodity) rows from CREA", len(rows))
    return rows


def aggregate(rows: list[dict]) -> dict[str, dict]:
    """Aggregate per destination ISO2: total €, total tonnes, commodity breakdown."""
    by_country: dict[str, dict] = {}
    for r in rows:
        cc = (r.get("destination_iso2") or "").upper()
        if len(cc) != 2 or not cc.isalpha():
            continue  # skip supranational aggregates / unknown destinations
        eur = float(r.get("value_eur") or 0)
        tonne = float(r.get("value_tonne") or 0)
        if eur <= 0 and tonne <= 0:
            continue
        agg = by_country.setdefault(cc, {"total_eur": 0.0, "total_tonne": 0.0, "commodities": []})
        agg["total_eur"] += eur
        agg["total_tonne"] += tonne
        agg["commodities"].append({
            "group": r.get("commodity_group"),
            "name": r.get("commodity_group_name") or r.get("commodity_group"),
            "value_eur": round(eur, 2),
            "value_tonne": round(tonne, 2),
        })
    # rank importers by euros paid to Russia; keep commodity lists big→small
    ranked = sorted(by_country.items(), key=lambda kv: -kv[1]["total_eur"])
    for i, (_cc, agg) in enumerate(ranked, start=1):
        agg["world_rank"] = i
        agg["commodities"].sort(key=lambda c: -c["value_eur"])
    return by_country


def load_to_db(by_country: dict[str, dict]):
    # psycopg2 takes the libpq URL directly — robust to missing port / special chars.
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    count = 0
    for cc, agg in by_country.items():
        cur.execute("""
            INSERT INTO ru_fossil_imports (country_code, total_eur, total_tonne,
                                           commodities, world_rank, period_from, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (country_code) DO UPDATE SET
                total_eur = EXCLUDED.total_eur,
                total_tonne = EXCLUDED.total_tonne,
                commodities = EXCLUDED.commodities,
                world_rank = EXCLUDED.world_rank,
                period_from = EXCLUDED.period_from,
                updated_at = NOW()
        """, (cc, agg["total_eur"], agg["total_tonne"],
              json.dumps(agg["commodities"], ensure_ascii=False),
              agg["world_rank"], PERIOD_FROM))
        count += 1
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Loaded %d importers into ru_fossil_imports.", count)


def run_once():
    rows = fetch_counter()
    by_country = aggregate(rows)
    logger.info("Aggregated %d importing countries", len(by_country))
    if by_country:
        load_to_db(by_country)


def main():
    parser = argparse.ArgumentParser(description="CREA Russian fossil imports loader")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=86400, help="seconds (default 1d)")
    args = parser.parse_args()
    if args.loop:
        logger.info("Starting CREA loader loop (interval: %ds)", args.interval)
        while True:
            try:
                run_once()
            except Exception as e:  # noqa: BLE001
                logger.error("CREA loader failed: %s", e)
            logger.info("Sleeping %ds...", args.interval)
            time.sleep(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
