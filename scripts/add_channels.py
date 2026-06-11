#!/usr/bin/env python3
"""
Add new TG channels to sources DB and subscribe via Telethon.
Run on VPS: python3 /opt/cis-thermometer/scripts/add_channels.py
"""
import asyncio
import os
import sys
import time
import psycopg2

# Channels to add: (country, username, display_name, tier)
NEW_CHANNELS = [
    # KZ
    ("KZ", "zakonkz", "Zakon.kz TG", "mainstream"),
    ("KZ", "ztb_qaz", "ZTB News TG", "mainstream"),
    ("KZ", "kozachkow", "Kozachkov Offside TG", "independent"),
    ("KZ", "vlastkz", "Власть.kz TG", "independent"),
    ("KZ", "nehabar", "НЕ Хабар TG", "independent"),
    ("KZ", "Zanamiviehali", "За нами выехали TG", "independent"),
    ("KZ", "yedilov_online", "Yedilov Online TG", "independent"),
    ("KZ", "qazaqstantv", "Qazaqstan TV TG", "state"),
    ("KZ", "astana_arnasy", "Astana TV TG", "state"),
    ("KZ", "RedKazakh", "RedKazakh TG", "opposition"),
    ("KZ", "respublikaKZmediaNEWS", "Республика.kz TG", "opposition"),
    ("KZ", "centralasiamedia", "Central Asia Media TG", "independent"),

    # UZ
    ("UZ", "gazetauz", "Gazeta.uz TG", "mainstream"),
    ("UZ", "podrobno", "Podrobno.uz TG", "mainstream"),
    ("UZ", "daraborot_uz", "Daryo TG", "mainstream"),
    ("UZ", "kun_uz", "Kun.uz TG", "mainstream"),
    ("UZ", "uzbekistan24_official", "Uzbekistan 24 TG", "state"),

    # AM
    ("AM", "news_am", "News.am TG", "mainstream"),

    # AZ
    ("AZ", "ReportAzNews", "Report.az TG", "state"),

    # GE
    ("GE", "publika_ge", "Publika GE TG", "independent"),
    ("GE", "onaborge", "On.ge TG", "mainstream"),

    # BY
    ("BY", "belapartisan", "Белорусский партизан TG", "opposition"),
    ("BY", "chart97", "Хартия 97 TG", "opposition"),
    ("BY", "motaborlkohelp", "Мотолько TG", "opposition"),
    ("BY", "baborelta", "БелТА TG", "state"),

    # MD
    ("MD", "newsmaker_md", "Newsmaker MD TG", "independent"),
    ("MD", "jurnaltv", "Jurnal.md TG", "independent"),

    # TJ
    ("TJ", "khovar_tj", "Khovar TG", "state"),

    # TM
    ("TM", "turkmenportal_official", "Turkmenportal TG", "state"),

    # KG
    ("KG", "kloopnews", "Kloop.kg TG", "independent"),
    ("KG", "kabar_kg", "Кабар KG TG", "state"),
]

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def add_to_db():
    """Add channels to sources table."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    added = 0
    skipped = 0
    
    for country, username, name, tier in NEW_CHANNELS:
        url = f"https://t.me/{username}"
        # Check if already exists
        cur.execute("SELECT id FROM sources WHERE url = %s", (url,))
        if cur.fetchone():
            print(f"  SKIP {username} — already exists")
            skipped += 1
            continue
        
        cur.execute("""
            INSERT INTO sources (name, url, country_code, source_type, tier, active, created_at)
            VALUES (%s, %s, %s, 'telegram', %s, true, NOW())
            RETURNING id
        """, (name, url, country, tier))
        sid = cur.fetchone()[0]
        print(f"  ADD [{country}] @{username} → id={sid} ({tier})")
        added += 1
    
    conn.commit()
    conn.close()
    print(f"\nDB: {added} added, {skipped} skipped")
    return added


async def subscribe_channels():
    """Subscribe to channels via Telethon."""
    try:
        from telethon import TelegramClient
        from telethon.tl.functions.channels import JoinChannelRequest
    except ImportError:
        print("Telethon not installed, skipping subscription")
        return
    
    session_path = "/opt/cis-thermometer/sessions/geopulse_vox"
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    
    client = TelegramClient(session_path, api_id, api_hash)
    await client.start()
    
    joined = 0
    failed = 0
    
    for country, username, name, tier in NEW_CHANNELS:
        try:
            print(f"  JOIN @{username}...", end=" ", flush=True)
            await client(JoinChannelRequest(username))
            print("✅")
            joined += 1
            time.sleep(8)  # avoid flood wait
        except Exception as e:
            err = str(e)
            if "already" in err.lower() or "participant" in err.lower():
                print("(already joined)")
                joined += 1
            elif "flood" in err.lower():
                print(f"⏳ flood wait: {err}")
                time.sleep(30)
                failed += 1
            else:
                print(f"❌ {err[:80]}")
                failed += 1
    
    await client.disconnect()
    print(f"\nTelethon: {joined} joined, {failed} failed")


if __name__ == "__main__":
    print("=== Adding channels to DB ===")
    add_to_db()
    
    print("\n=== Subscribing via Telethon ===")
    asyncio.run(subscribe_channels())
