#!/usr/bin/env python3
"""Load real trade data from UN Comtrade API into GEO PULSE database."""

import requests
import psycopg2
import time

API_KEY = "6f943d47ae2d4ac598c200a8665aaf33"
BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

COUNTRIES = {
    "AM": 51, "AZ": 31, "BY": 112, "GE": 268, "KZ": 398,
    "KG": 417, "MD": 498, "TJ": 762, "TM": 795, "UZ": 860,
}

RUSSIA_CODE = 643
YEARS = list(range(2012, 2025))

DB_PARAMS = {
    "host": "db", "port": 5432,
    "dbname": "cis_thermometer", "user": "thermo",
    "password": "thermo",
}


def fetch_trade(reporter_code, partner_code, periods, api_key):
    """Fetch TOTAL trade data, filter for mot=0 & partner2=0 (aggregate)."""
    all_data = []
    for i in range(0, len(periods), 5):
        batch = periods[i:i+5]
        period_str = ",".join(str(y) for y in batch)
        url = (f"{BASE_URL}?reporterCode={reporter_code}&partnerCode={partner_code}"
               f"&period={period_str}&flowCode=M,X&cmdCode=TOTAL"
               f"&subscription-key={api_key}")
        
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"    Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                # Filter: motCode=0 (all transport), partner2Code=0 (no secondary partner)
                filtered = [r for r in data.get("data", [])
                           if r.get("motCode") == 0 and r.get("partner2Code") == 0]
                all_data.extend(filtered)
                break
            except Exception as e:
                print(f"    Error (attempt {attempt+1}): {e}")
                time.sleep(5)
        
        time.sleep(1.5)  # Rate limit between batches
    
    return all_data


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    
    cur.execute("DELETE FROM trade_data")
    conn.commit()
    print("Cleared old trade_data\n")
    
    total_inserted = 0
    
    for country_code, m49 in sorted(COUNTRIES.items()):
        print(f"{'='*50}")
        print(f"{country_code} (M49={m49}) ↔ Russia")
        
        # Country as reporter
        records = fetch_trade(m49, RUSSIA_CODE, YEARS, API_KEY)
        print(f"  Country-reported: {len(records)} records")
        
        time.sleep(2)
        
        # Russia as reporter (fallback for missing years)
        records_ru = fetch_trade(RUSSIA_CODE, m49, YEARS, API_KEY)
        print(f"  Russia-reported: {len(records_ru)} records")
        
        # Organize by year — prefer country-reported data
        year_data = {}
        
        for r in records:
            year = int(r["period"])
            if year not in year_data:
                year_data[year] = {"ru_export": None, "ru_import": None}
            if r["flowCode"] == "M":
                # Country imports from Russia = Russia exports to country
                year_data[year]["ru_export"] = r["primaryValue"]
            elif r["flowCode"] == "X":
                # Country exports to Russia = Russia imports from country
                year_data[year]["ru_import"] = r["primaryValue"]
        
        # Fill gaps from Russia's perspective
        for r in records_ru:
            year = int(r["period"])
            if year not in year_data:
                year_data[year] = {"ru_export": None, "ru_import": None}
            if r["flowCode"] == "X" and year_data[year]["ru_export"] is None:
                year_data[year]["ru_export"] = r["primaryValue"]
            elif r["flowCode"] == "M" and year_data[year]["ru_import"] is None:
                year_data[year]["ru_import"] = r["primaryValue"]
        
        # Insert
        prev_total = None
        for year in sorted(year_data.keys()):
            d = year_data[year]
            ru_export = d["ru_export"] or 0
            ru_import = d["ru_import"] or 0
            total = ru_export + ru_import
            balance = ru_export - ru_import
            
            yoy = None
            if prev_total and prev_total > 0:
                yoy = round((total - prev_total) / prev_total * 100, 1)
            prev_total = total
            
            cur.execute("""
                INSERT INTO trade_data (country_code, year, ru_export_usd, ru_import_usd,
                                        total_trade_usd, trade_balance_usd, yoy_change_pct)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (country_code, year, ru_export, ru_import, total, balance, yoy))
            
            total_inserted += 1
            exp_b = ru_export / 1e9
            imp_b = ru_import / 1e9
            tot_b = total / 1e9
            print(f"  {year}: RU→{country_code} ${exp_b:.2f}B | {country_code}→RU ${imp_b:.2f}B | Total ${tot_b:.2f}B")
        
        conn.commit()
        time.sleep(2)
    
    print(f"\n{'='*50}")
    print(f"DONE! {total_inserted} records inserted\n")
    
    cur.execute("""
        SELECT country_code, count(*), min(year), max(year), 
               round(max(total_trade_usd)/1e9::numeric, 1) as peak_B
        FROM trade_data GROUP BY country_code ORDER BY country_code
    """)
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} years ({row[2]}-{row[3]}), peak ${row[4]}B")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
