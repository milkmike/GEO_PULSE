#!/usr/bin/env python3
"""Load UN General Assembly voting agreement data (Russia vs the world).

Source: Harvard Dataverse - Erik Voeten UN Voting Dataset
Uses pre-computed agreement scores (AgreementScoresAll_Jun2024.csv).
Covers every UN-member country in the GEO PULSE registry (98 of 99;
Taiwan is not a UN member). Feeds the structural layer of the RRI.
"""
import csv
import os
import sys
import urllib.request

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:thermo@localhost:5432/cis_thermometer")

# COW (Correlates of War) country codes -> our ISO2 (full registry coverage)
COW_TO_ISO2 = {
    # Постсоветское пространство
    705: "KZ", 371: "AM", 704: "UZ", 703: "KG", 702: "TJ",
    701: "TM", 373: "AZ", 372: "GE", 359: "MD", 370: "BY", 369: "UA",
    # Европа
    200: "GB", 205: "IE", 210: "NL", 211: "BE", 220: "FR", 225: "CH",
    230: "ES", 235: "PT", 255: "DE", 290: "PL", 305: "AT", 310: "HU",
    316: "CZ", 317: "SK", 325: "IT", 339: "AL", 341: "ME", 343: "MK",
    344: "HR", 345: "RS", 346: "BA", 349: "SI", 350: "GR", 352: "CY",
    355: "BG", 360: "RO", 366: "EE", 367: "LV", 368: "LT", 375: "FI",
    380: "SE", 385: "NO", 390: "DK", 395: "IS",
    # Америки
    2: "US", 20: "CA", 40: "CU", 70: "MX", 93: "NI", 100: "CO",
    101: "VE", 135: "PE", 140: "BR", 145: "BO", 155: "CL", 160: "AR",
    # Африка
    432: "ML", 436: "NE", 439: "BF", 475: "NG", 482: "CF", 501: "KE",
    530: "ET", 560: "ZA", 600: "MA", 615: "DZ", 616: "TN", 620: "LY",
    625: "SD",
    # Ближний Восток
    630: "IR", 640: "TR", 645: "IQ", 651: "EG", 652: "SY", 666: "IL",
    670: "SA", 694: "QA", 696: "AE",
    # Азия и Океания
    700: "AF", 710: "CN", 712: "MN", 731: "KP", 732: "KR", 740: "JP",
    750: "IN", 770: "PK", 771: "BD", 775: "MM", 780: "LK", 800: "TH",
    816: "VN", 820: "MY", 830: "SG", 840: "PH", 850: "ID",
    900: "AU", 920: "NZ",
}
RU_COW = 365

DATA_URL = "https://dataverse.harvard.edu/api/access/datafile/10295876"
LOCAL_PATH = "/tmp/un_agreement.csv"


def download_data():
    """Download agreement scores CSV."""
    print(f"Downloading from {DATA_URL}...")
    req = urllib.request.Request(DATA_URL, headers={
        "User-Agent": "GeoPulse/1.0 (research; https://github.com/milkmike/GEO_PULSE)",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            with open(LOCAL_PATH, "wb") as f:
                f.write(data)
        print(f"Downloaded {len(data)} bytes to {LOCAL_PATH}")
    except Exception as e:
        # Try with redirect following
        import http.client
        import ssl
        print(f"Direct download failed ({e}), trying with redirect...")
        req2 = urllib.request.Request(DATA_URL, headers={
            "User-Agent": "Mozilla/5.0 (compatible; GeoPulse/1.0)",
        })
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
        with opener.open(req2, timeout=60) as resp:
            data = resp.read()
            with open(LOCAL_PATH, "wb") as f:
                f.write(data)
        print(f"Downloaded {len(data)} bytes to {LOCAL_PATH}")


def parse_data():
    """Parse CSV and extract Russia-CIS agreement data (2012+)."""
    results = {}  # (country_code, year) -> {agree, nvotes}
    
    with open(LOCAL_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            c1 = int(row["ccode1"])
            c2 = int(row["ccode2"])
            year = int(row["year"])
            
            if year < 2012:
                continue
            
            # Find partner country
            partner = None
            if c1 == RU_COW and c2 in COW_TO_ISO2:
                partner = COW_TO_ISO2[c2]
                nvotes = int(row.get("NVotesAll.y", "0") or 0)
            elif c2 == RU_COW and c1 in COW_TO_ISO2:
                partner = COW_TO_ISO2[c1]
                nvotes = int(row.get("NVotesAll.x", "0") or 0)
            
            if not partner:
                continue
            
            key = (partner, year)
            if key not in results:
                agree_pct = float(row["agree"])
                # NVotesAll is the total votes for the country in that session
                # agree is already the proportion of agreement
                total = nvotes if nvotes > 0 else 80  # fallback
                agree_count = int(round(agree_pct * total))
                disagree_count = total - agree_count
                
                results[key] = {
                    "total_votes": total,
                    "agree_with_russia": agree_count,
                    "disagree_with_russia": disagree_count,
                    "abstain": 0,  # Not tracked separately in agreement scores
                    "agreement_pct": round(agree_pct * 100, 1),
                }
    
    return results


def load_to_db(results):
    """Insert/update data in database."""
    # Parse DB URL
    import re
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", DB_URL)
    if not m:
        print(f"Cannot parse DATABASE_URL: {DB_URL}")
        sys.exit(1)
    
    conn = psycopg2.connect(
        host=m.group(3), port=int(m.group(4)),
        user=m.group(1), password=m.group(2),
        dbname=m.group(5)
    )
    cur = conn.cursor()
    
    count = 0
    for (country_code, year), data in sorted(results.items()):
        cur.execute("""
            INSERT INTO un_votes (country_code, year, total_votes, agree_with_russia,
                                  disagree_with_russia, abstain, agreement_pct, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (country_code, year)
            DO UPDATE SET
                total_votes = EXCLUDED.total_votes,
                agree_with_russia = EXCLUDED.agree_with_russia,
                disagree_with_russia = EXCLUDED.disagree_with_russia,
                abstain = EXCLUDED.abstain,
                agreement_pct = EXCLUDED.agreement_pct,
                updated_at = NOW()
        """, (
            country_code, year,
            data["total_votes"], data["agree_with_russia"],
            data["disagree_with_russia"], data["abstain"],
            data["agreement_pct"]
        ))
        count += 1
        print(f"  {country_code} {year}: {data['agreement_pct']:.1f}% ({data['agree_with_russia']}/{data['total_votes']})")
    
    conn.commit()
    cur.close()
    conn.close()
    print(f"\nLoaded {count} records into un_votes table.")


def run_once():
    """Download and load UN voting data."""
    # Remove cached file to force re-download (source might have updated)
    if os.path.exists(LOCAL_PATH):
        os.remove(LOCAL_PATH)
    download_data()
    results = parse_data()
    print(f"\nParsed {len(results)} country-year records")
    load_to_db(results)
    print("Done!")


def main():
    import argparse
    import time
    import logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run in loop mode")
    parser.add_argument("--interval", type=int, default=2592000,
                        help="Interval in seconds (default: 30 days)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [un-votes] %(levelname)s: %(message)s")
    logger = logging.getLogger(__name__)

    if args.loop:
        logger.info(f"Starting UN votes loader loop (interval: {args.interval}s)")
        while True:
            try:
                print("=== UN Voting Agreement Data Loader ===")
                run_once()
            except Exception as e:
                logger.error(f"UN votes loader failed: {e}")
            logger.info(f"Sleeping {args.interval}s...")
            time.sleep(args.interval)
    else:
        print("=== UN Voting Agreement Data Loader ===")
        run_once()


if __name__ == "__main__":
    main()
