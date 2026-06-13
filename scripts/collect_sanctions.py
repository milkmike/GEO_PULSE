#!/usr/bin/env python3
"""Sanctions pressure per jurisdiction — structural layer (OpenSanctions).

Source: OpenSanctions public catalog index (free, no key):
    https://data.opensanctions.org/datasets/latest/index.json

For every dataset in the `sanctions` collection that has a publisher country we
aggregate, per sanctioning jurisdiction (ISO2): number of lists, total targets,
newest list change, and the program breakdown. Upsert into `sanctions_pressure`,
recording the delta vs the previous snapshot so the signals engine can flag
escalations.

NOTE (scaffold): this is the *jurisdiction-level* sanctions baseline — counts
cover all targets a jurisdiction sanctions, not only Russia-linked ones. The
Russia-specific refinement (filter targets whose `countries` include `ru` via
the OpenSanctions targets.simple.csv) and the RRI weight calibration are the
documented next steps — see docs/research/worldmonitor-layers.md §7.

Run:
    python scripts/collect_sanctions.py            # one-shot
    python scripts/collect_sanctions.py --loop     # monthly refresh
"""
import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:thermo@localhost:5432/cis_thermometer")
INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sanctions] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def fetch_index() -> list[dict]:
    """Download the OpenSanctions catalog index and return its datasets."""
    req = urllib.request.Request(INDEX_URL, headers={
        "User-Agent": "GeoPulse/1.0 (research; https://github.com/milkmike/GEO_PULSE)",
    })
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    datasets = data.get("datasets", [])
    logger.info("Fetched %d datasets from OpenSanctions catalog", len(datasets))
    return datasets


def aggregate(datasets: list[dict]) -> dict[str, dict]:
    """Aggregate sanctions lists per publisher jurisdiction (ISO2 upper)."""
    by_country: dict[str, dict] = {}
    for d in datasets:
        if "sanctions" not in (d.get("collections") or []):
            continue
        pub = d.get("publisher") or {}
        cc = (pub.get("country") or "").upper()
        if not cc or len(cc) != 2:
            continue  # supranational/unknown publishers without a 2-letter code
        targets = int(d.get("target_count") or 0)
        last_change = (d.get("last_change") or "")[:10] or None

        agg = by_country.setdefault(cc, {
            "lists_count": 0, "target_count": 0, "last_change": None, "programs": [],
        })
        agg["lists_count"] += 1
        agg["target_count"] += targets
        if last_change and (agg["last_change"] is None or last_change > agg["last_change"]):
            agg["last_change"] = last_change
        agg["programs"].append({
            "name": d.get("name"), "title": d.get("title"),
            "targets": targets, "last_change": last_change,
        })
    return by_country


def load_to_db(by_country: dict[str, dict]):
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", DB_URL)
    if not m:
        logger.error("Cannot parse DATABASE_URL: %s", DB_URL)
        sys.exit(1)
    conn = psycopg2.connect(host=m.group(3), port=int(m.group(4)),
                            user=m.group(1), password=m.group(2), dbname=m.group(5))
    cur = conn.cursor()
    count = 0
    for cc, agg in sorted(by_country.items(), key=lambda kv: -kv[1]["target_count"]):
        # delta vs previous snapshot
        cur.execute("SELECT target_count FROM sanctions_pressure WHERE country_code = %s", (cc,))
        row = cur.fetchone()
        prev = int(row[0]) if row else 0
        delta = agg["target_count"] - prev

        cur.execute("""
            INSERT INTO sanctions_pressure (country_code, lists_count, target_count,
                                            prev_target_count, delta, last_change,
                                            programs, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (country_code) DO UPDATE SET
                lists_count = EXCLUDED.lists_count,
                target_count = EXCLUDED.target_count,
                prev_target_count = sanctions_pressure.target_count,
                delta = EXCLUDED.target_count - sanctions_pressure.target_count,
                last_change = EXCLUDED.last_change,
                programs = EXCLUDED.programs,
                updated_at = NOW()
        """, (cc, agg["lists_count"], agg["target_count"], prev, delta,
              agg["last_change"], json.dumps(agg["programs"], ensure_ascii=False)))
        count += 1
        logger.info("  %s: %d lists, %d targets (Δ%+d), last %s",
                    cc, agg["lists_count"], agg["target_count"], delta, agg["last_change"])
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Loaded %d jurisdictions into sanctions_pressure.", count)


def run_once():
    datasets = fetch_index()
    by_country = aggregate(datasets)
    logger.info("Aggregated %d sanctioning jurisdictions", len(by_country))
    load_to_db(by_country)


def main():
    parser = argparse.ArgumentParser(description="OpenSanctions pressure loader")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=2592000, help="seconds (default 30d)")
    args = parser.parse_args()

    if args.loop:
        logger.info("Starting sanctions loader loop (interval: %ds)", args.interval)
        while True:
            try:
                run_once()
            except Exception as e:  # noqa: BLE001
                logger.error("Sanctions loader failed: %s", e)
            logger.info("Sleeping %ds...", args.interval)
            time.sleep(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
