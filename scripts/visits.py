"""Visitor stats report — daily views/uniques + top pages & referrers.

The detailed visitor data lives behind the admin guard (and nginx blocks
/api/v1/admin/ from the public), so this is the practical way to read it on the
server:

    docker compose run --rm api python scripts/visits.py [--days 30]

(or curl http://127.0.0.1:8100/api/v1/admin/visits -H "X-Admin-Key: <key>")
"""
import argparse

from sqlalchemy import text

from src.db import get_session


def main():
    ap = argparse.ArgumentParser(description="Pageview / visitor stats")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    d = {"d": args.days}

    with get_session() as session:
        t = session.execute(text(
            "SELECT COUNT(*) v, COUNT(DISTINCT visitor_hash) u "
            "FROM pageviews WHERE day > CURRENT_DATE - :d"), d).fetchone()
        print(f"\n=== Посещения за {args.days} дн.: "
              f"{t.v or 0} визитов, {t.u or 0} уникальных ===\n")

        print("День         Визиты  Уники")
        for r in session.execute(text(
            "SELECT day, COUNT(*) v, COUNT(DISTINCT visitor_hash) u "
            "FROM pageviews WHERE day > CURRENT_DATE - :d "
            "GROUP BY day ORDER BY day DESC"), d):
            print(f"{r.day}   {r.v:6d}  {r.u:5d}")

        print("\nТоп-страницы:")
        for r in session.execute(text(
            "SELECT path, COUNT(*) v FROM pageviews WHERE day > CURRENT_DATE - :d "
            "GROUP BY path ORDER BY v DESC LIMIT 15"), d):
            print(f"  {r.v:6d}  {r.path}")

        print("\nТоп-рефереры:")
        rows = session.execute(text(
            "SELECT referrer_host, COUNT(*) v FROM pageviews "
            "WHERE day > CURRENT_DATE - :d AND referrer_host IS NOT NULL "
            "AND referrer_host <> '' GROUP BY referrer_host ORDER BY v DESC LIMIT 15"), d).fetchall()
        if rows:
            for r in rows:
                print(f"  {r.v:6d}  {r.referrer_host}")
        else:
            print("  (рефереров нет — прямые заходы)")
        print()


if __name__ == "__main__":
    main()
