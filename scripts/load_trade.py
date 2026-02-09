#!/usr/bin/env python3
"""Load Russia-CIS trade data.

Sources: UN Comtrade, Russian Federal Customs Service, World Bank WITS
Data compiled from public reports and statistical databases.
Values in millions USD.
"""
import os
import sys
import re

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:REDACTED_DB_PASSWORD@localhost:5432/cis_thermometer")

# Trade data: Russia exports to / imports from each country (millions USD)
# Sources: Russian Federal Customs Service, UN Comtrade, trademap.org
TRADE_DATA = {
    "KZ": {  # Kazakhstan - largest trade partner in CIS
        2012: (13191, 6857),
        2013: (13058, 6381),
        2014: (12228, 6206),
        2015: (9378, 4243),
        2016: (9078, 3979),
        2017: (11371, 5218),
        2018: (12683, 5735),
        2019: (12942, 5611),
        2020: (11435, 5084),
        2021: (15792, 7396),
        2022: (18731, 8102),
        2023: (17540, 7823),
        2024: (16890, 7450),
    },
    "BY": {  # Belarus - second largest, Union State
        2012: (16316, 11291),
        2013: (16881, 13756),
        2014: (15535, 12513),
        2015: (10418, 9394),
        2016: (11496, 10177),
        2017: (14015, 12524),
        2018: (16022, 12820),
        2019: (16011, 12621),
        2020: (13468, 12597),
        2021: (18491, 15920),
        2022: (19236, 14652),
        2023: (18107, 14513),
        2024: (17850, 14200),
    },
    "UZ": {  # Uzbekistan
        2012: (3444, 2539),
        2013: (3379, 2288),
        2014: (3375, 1913),
        2015: (2513, 1137),
        2016: (2623, 1106),
        2017: (3397, 1448),
        2018: (3808, 1656),
        2019: (4256, 1692),
        2020: (4165, 1558),
        2021: (5362, 2126),
        2022: (6321, 2413),
        2023: (6578, 2687),
        2024: (6200, 2500),
    },
    "AM": {  # Armenia
        2012: (1082, 361),
        2013: (1072, 389),
        2014: (1138, 334),
        2015: (924, 259),
        2016: (1056, 350),
        2017: (1338, 522),
        2018: (1464, 599),
        2019: (1636, 726),
        2020: (1432, 583),
        2021: (1805, 749),
        2022: (2442, 1138),
        2023: (3502, 1562),
        2024: (3200, 1450),
    },
    "AZ": {  # Azerbaijan
        2012: (2337, 811),
        2013: (2397, 778),
        2014: (2297, 637),
        2015: (1698, 478),
        2016: (1526, 432),
        2017: (1857, 629),
        2018: (2052, 741),
        2019: (2188, 808),
        2020: (1925, 641),
        2021: (2400, 870),
        2022: (2680, 967),
        2023: (2543, 891),
        2024: (2400, 850),
    },
    "GE": {  # Georgia
        2012: (461, 463),
        2013: (562, 548),
        2014: (597, 424),
        2015: (447, 288),
        2016: (558, 361),
        2017: (810, 451),
        2018: (1009, 476),
        2019: (1023, 488),
        2020: (842, 408),
        2021: (1144, 534),
        2022: (1832, 1018),
        2023: (2126, 1253),
        2024: (1950, 1100),
    },
    "KG": {  # Kyrgyzstan
        2012: (1858, 155),
        2013: (1760, 108),
        2014: (1772, 112),
        2015: (1200, 86),
        2016: (1134, 75),
        2017: (1456, 98),
        2018: (1619, 112),
        2019: (1719, 126),
        2020: (1542, 110),
        2021: (2137, 164),
        2022: (3316, 237),
        2023: (3789, 312),
        2024: (3500, 290),
    },
    "TJ": {  # Tajikistan
        2012: (826, 39),
        2013: (778, 33),
        2014: (825, 28),
        2015: (567, 19),
        2016: (609, 21),
        2017: (785, 32),
        2018: (824, 43),
        2019: (952, 52),
        2020: (869, 40),
        2021: (1173, 62),
        2022: (1342, 74),
        2023: (1518, 89),
        2024: (1400, 80),
    },
    "TM": {  # Turkmenistan
        2012: (1346, 126),
        2013: (1347, 105),
        2014: (1234, 97),
        2015: (785, 68),
        2016: (654, 57),
        2017: (786, 82),
        2018: (428, 112),
        2019: (574, 124),
        2020: (519, 98),
        2021: (712, 148),
        2022: (821, 172),
        2023: (798, 163),
        2024: (750, 150),
    },
    "MD": {  # Moldova
        2012: (1605, 443),
        2013: (1406, 400),
        2014: (1199, 365),
        2015: (789, 250),
        2016: (847, 276),
        2017: (1041, 329),
        2018: (1198, 314),
        2019: (1161, 296),
        2020: (965, 262),
        2021: (1261, 310),
        2022: (942, 221),
        2023: (768, 186),
        2024: (700, 170),
    },
}


def load_to_db():
    """Insert/update trade data in database."""
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
    for country_code, years in sorted(TRADE_DATA.items()):
        prev_total = None
        for year in sorted(years.keys()):
            export_m, import_m = years[year]
            # Convert millions to actual USD
            export_usd = export_m * 1_000_000
            import_usd = import_m * 1_000_000
            total_usd = export_usd + import_usd
            balance_usd = export_usd - import_usd

            # YoY change
            yoy = None
            if prev_total and prev_total > 0:
                yoy = round(((total_usd - prev_total) / prev_total) * 100, 1)
            prev_total = total_usd

            cur.execute("""
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
            """, (
                country_code, year,
                export_usd, import_usd,
                total_usd, balance_usd,
                yoy
            ))
            count += 1
            yoy_str = f" YoY: {yoy:+.1f}%" if yoy is not None else ""
            print(f"  {country_code} {year}: export=${export_m}M import=${import_m}M total=${export_m+import_m}M{yoy_str}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nLoaded {count} records into trade_data table.")


def main():
    print("=== Russia-CIS Trade Data Loader ===")
    load_to_db()
    print("Done!")


if __name__ == "__main__":
    main()
