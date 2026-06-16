#!/usr/bin/env python3
"""Financial "isolation radar" for Russia — single-row composite snapshot.

Combines signals we can source for free:
  * ruble strength — USD/RUB and CNY/RUB from our CBR `fx_rates` table;
  * Moscow Exchange index (IMOEX) — MOEX ISS public API (no key).

Computes ~30-day changes and an indicative pressure verdict (0..2), then upserts
the single row of `ru_market_radar`. This is the worldmonitor "market radar"
analog through the Russia lens — a quick read of financial pressure / isolation.

Run:
    python scripts/collect_market_radar.py            # one-shot
    python scripts/collect_market_radar.py --loop     # periodic refresh
"""
import argparse
import json
import logging
import os
import time
import urllib.request
from datetime import date, timedelta

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:thermo@localhost:5432/cis_thermometer")
ISS_CUR = "https://iss.moex.com/iss/engines/stock/markets/index/securities/IMOEX.json?iss.meta=off"
ISS_HIST = "https://iss.moex.com/iss/history/engines/stock/markets/index/securities/IMOEX.json?from={frm}&iss.meta=off"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [radar] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "GeoPulse/1.0 (research)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def fetch_moex() -> dict:
    """Current IMOEX level + ~30d change + a small close sparkline."""
    out: dict = {"moex": None, "moex_chg30": None, "moex_spark": []}
    try:
        cur = _get_json(ISS_CUR)
        md = cur.get("marketdata", {})
        cols, data = md.get("columns", []), md.get("data", [])
        if data:
            rec = dict(zip(cols, data[0]))
            out["moex"] = rec.get("CURRENTVALUE") or rec.get("LASTVALUE")
    except Exception as e:  # noqa: BLE001
        logger.warning("MOEX current fetch failed: %s", e)

    try:
        frm = (date.today() - timedelta(days=45)).isoformat()
        h = _get_json(ISS_HIST.format(frm=frm)).get("history", {})
        hc, hd = h.get("columns", []), h.get("data", [])
        ci, ti = hc.index("CLOSE"), hc.index("TRADEDATE")
        closes = [(r[ti], float(r[ci])) for r in hd if r[ci] is not None]
        out["moex_spark"] = [{"d": d, "v": round(v, 2)} for d, v in closes[-30:]]
        if len(closes) >= 2:
            first = closes[0][1]
            last = out["moex"] or closes[-1][1]
            if first:
                out["moex_chg30"] = round((last - first) / first * 100, 2)
            if out["moex"] is None:
                out["moex"] = closes[-1][1]
    except Exception as e:  # noqa: BLE001
        logger.warning("MOEX history fetch failed: %s", e)
    return out


def fetch_fx(cur, currency: str) -> tuple[float | None, float | None]:
    """Latest rate_to_rub and % change vs ~30 days ago for a currency."""
    cur.execute("SELECT rate_to_rub FROM fx_rates WHERE currency=%s ORDER BY day DESC LIMIT 1", (currency,))
    row = cur.fetchone()
    latest = float(row[0]) if row else None
    cur.execute(
        "SELECT rate_to_rub FROM fx_rates WHERE currency=%s AND day <= CURRENT_DATE - INTERVAL '30 days' "
        "ORDER BY day DESC LIMIT 1",
        (currency,),
    )
    row = cur.fetchone()
    prev = float(row[0]) if row else None
    chg = round((latest - prev) / prev * 100, 2) if (latest and prev) else None
    return latest, chg


def verdict_for(usd_chg30: float | None, moex_chg30: float | None) -> tuple[int, str]:
    """Indicative composite: weaker ruble + falling MOEX = more financial pressure."""
    pressure = 0
    if usd_chg30 is not None and usd_chg30 > 2:   # ruble lost >2% to the dollar
        pressure += 1
    if moex_chg30 is not None and moex_chg30 < -2:  # market down >2%
        pressure += 1
    label = {0: "Стабильно", 1: "Умеренное давление", 2: "Давление растёт"}[pressure]
    return pressure, label


def run_once():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    usd_rub, usd_chg = fetch_fx(cur, "USD")
    cny_rub, cny_chg = fetch_fx(cur, "CNY")
    m = fetch_moex()
    pressure, verdict = verdict_for(usd_chg, m["moex_chg30"])
    cur.execute("""
        INSERT INTO ru_market_radar (id, usd_rub, usd_rub_chg30, cny_rub, cny_rub_chg30,
                                     moex, moex_chg30, moex_spark, pressure, verdict, updated_at)
        VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            usd_rub = EXCLUDED.usd_rub, usd_rub_chg30 = EXCLUDED.usd_rub_chg30,
            cny_rub = EXCLUDED.cny_rub, cny_rub_chg30 = EXCLUDED.cny_rub_chg30,
            moex = EXCLUDED.moex, moex_chg30 = EXCLUDED.moex_chg30,
            moex_spark = EXCLUDED.moex_spark, pressure = EXCLUDED.pressure,
            verdict = EXCLUDED.verdict, updated_at = NOW()
    """, (usd_rub, usd_chg, cny_rub, cny_chg, m["moex"], m["moex_chg30"],
          json.dumps(m["moex_spark"]), pressure, verdict))
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Radar: USD/RUB=%s (%s%%), CNY/RUB=%s, MOEX=%s (%s%%) → %s",
                usd_rub, usd_chg, cny_rub, m["moex"], m["moex_chg30"], verdict)


def main():
    parser = argparse.ArgumentParser(description="Russia financial isolation radar")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=21600, help="seconds (default 6h)")
    args = parser.parse_args()
    if args.loop:
        logger.info("Starting market-radar loop (interval: %ds)", args.interval)
        while True:
            try:
                run_once()
            except Exception as e:  # noqa: BLE001
                logger.error("Radar failed: %s", e)
            time.sleep(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
