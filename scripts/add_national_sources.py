#!/usr/bin/env python3
"""Add national-language media sources to GEO PULSE."""

import psycopg2

DB_PARAMS = {
    "host": "db", "port": 5432,
    "dbname": "cis_thermometer", "user": "thermo",
    "password": "REDACTED_DB_PASSWORD",
}

# Format: (country_code, name, url, tier, language)
SOURCES = [
    # ── Kazakhstan (kk) ──
    ("KZ", "Kazinform KZ", "https://kazinform.kz/kz", "official", "kk"),
    ("KZ", "Tengrinews KZ", "https://tengrinews.kz/kz/", "mainstream", "kk"),
    ("KZ", "Inform.kz KZ", "https://inform.kz/kz", "official", "kk"),
    ("KZ", "Egemen Qazaqstan", "https://egemen.kz", "official", "kk"),
    ("KZ", "Qazaqstan TV KZ", "https://qazaqstan.tv/kz/news/", "official", "kk"),

    # ── Azerbaijan (az) ──
    ("AZ", "Report.az AZ", "https://report.az/az/", "mainstream", "az"),
    ("AZ", "APA AZ", "https://apa.az/az", "mainstream", "az"),
    ("AZ", "Trend AZ", "https://trend.az/azerbaijan/", "mainstream", "az"),
    ("AZ", "1news.az AZ", "https://1news.az/az", "mainstream", "az"),

    # ── Armenia (hy) ──
    ("AM", "1lurer.am", "https://1lurer.am", "official", "hy"),
    ("AM", "Hetq AM", "https://hetq.am/hy", "independent", "hy"),
    ("AM", "Aravot HY", "https://www.aravot.am/hy/", "domestic_opposition", "hy"),
    ("AM", "Tert.am HY", "https://tert.am/am/", "mainstream", "hy"),

    # ── Georgia (ka) ──
    ("GE", "Netgazeti KA", "https://netgazeti.ge/ka", "analytics", "ka"),
    ("GE", "Publika.ge", "https://publika.ge", "mainstream", "ka"),
    ("GE", "Civil.ge KA", "https://civil.ge/ka", "independent", "ka"),

    # ── Moldova (ro) ──
    ("MD", "Newsmaker RO", "https://newsmaker.md/ro/", "independent", "ro"),
    ("MD", "TV8.md", "https://tv8.md/ro/", "mainstream", "ro"),
    ("MD", "Moldpres RO", "https://moldpres.md", "official", "ro"),
    ("MD", "Deschide.md", "https://deschide.md", "independent", "ro"),

    # ── Kyrgyzstan (ky) ──
    ("KG", "Kaktus KG", "https://kaktus.media/kg/", "independent", "ky"),
    ("KG", "AKIpress KG", "https://akipress.org/kg/", "mainstream", "ky"),
    ("KG", "Kabar KG", "https://kabar.kg/news", "official", "ky"),

    # ── Tajikistan (tg) ──
    ("TJ", "Asia-Plus TJ", "https://asiaplustj.info/tj", "independent", "tg"),
    ("TJ", "Khovar TJ", "https://khovar.tj", "official", "tg"),
    ("TJ", "Ozodagon TJ", "https://ozodagon.com", "independent", "tg"),

    # ── Turkmenistan (tk) ──
    ("TM", "Turkmenistan.gov TK", "https://turkmenistan.gov.tm/tk", "official", "tk"),
    ("TM", "Turkmenportal TK", "https://turkmenportal.com/tk", "mainstream", "tk"),

    # ── Uzbekistan (uz) ──
    ("UZ", "Kun.uz UZ", "https://kun.uz/uz", "mainstream", "uz"),
    ("UZ", "Daryo UZ", "https://daryo.uz/uz", "mainstream", "uz"),
    ("UZ", "Qalampir UZ", "https://qalampir.uz/uz", "mainstream", "uz"),
    ("UZ", "Gazeta.uz UZ", "https://gazeta.uz/uz/", "mainstream", "uz"),
    ("UZ", "UzA UZ", "https://uza.uz/uz", "official", "uz"),

    # ── Belarus (be) ──
    ("BY", "Nasha Niva BE", "https://nashaniva.com/be/", "domestic_opposition", "be"),
    ("BY", "Svaboda BE", "https://www.svaboda.org/z/15870", "western_proxy", "be"),
]


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    # Check existing URLs
    cur.execute("SELECT url FROM sources")
    existing = {r[0] for r in cur.fetchall()}

    # Check table columns
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'sources' ORDER BY ordinal_position
    """)
    columns = [r[0] for r in cur.fetchall()]
    print(f"Columns: {columns}")
    has_language = "language" in columns

    if not has_language:
        print("Adding 'language' column to sources...")
        cur.execute("ALTER TABLE sources ADD COLUMN language VARCHAR(5) DEFAULT 'ru'")
        conn.commit()
        print("  Done. Existing sources set to 'ru'")

    added = 0
    skipped = 0
    for country, name, url, tier, lang in SOURCES:
        if url in existing:
            print(f"  SKIP (exists): {url}")
            skipped += 1
            continue

        cur.execute("""
            INSERT INTO sources (country_code, name, url, tier, language, active, source_type)
            VALUES (%s, %s, %s, %s, %s, true, 'web')
        """, (country, name, url, tier, lang))
        added += 1
        print(f"  ADD: [{country}] {name} ({lang}) — {tier}")

    conn.commit()

    # Summary
    print(f"\n{'='*50}")
    print(f"Added: {added}, Skipped: {skipped}")
    cur.execute("""
        SELECT country_code, language, count(*)
        FROM sources WHERE active = true
        GROUP BY country_code, language
        ORDER BY country_code, language
    """)
    print("\nSources by country & language:")
    for r in cur.fetchall():
        print(f"  {r[0]} [{r[1]}]: {r[2]}")

    cur.execute("SELECT count(*) FROM sources WHERE active = true")
    total = cur.fetchone()[0]
    print(f"\nTotal active sources: {total}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
