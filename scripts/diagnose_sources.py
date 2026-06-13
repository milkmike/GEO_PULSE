"""One-shot diagnostic: WHY are sources STALE/DEAD?

The collector (scripts/collect.py) swallows every fetch error and returns [],
so a source that stops producing articles silently drifts to DEAD after 7 days
(src/engine/health.py) with no recorded reason. This script closes that gap
*for diagnosis only* — it does not change the schema or collector behaviour.

For each active source it:
  1. reads the learned health status via source_health() (OK/STALE/DEAD),
  2. probes the live URL (rss/web) and classifies the failure reason,
  3. marks telegram sources as needing a Telethon session (collect.py skips
     them — they are gathered by the separate tg-collector),
  4. prints a summary by reason / type / tier and writes a markdown report.

Usage:
    python scripts/diagnose_sources.py                 # probe STALE+DEAD
    python scripts/diagnose_sources.py --all           # probe every source
    python scripts/diagnose_sources.py --deep          # also test web extraction
    python scripts/diagnose_sources.py --out report.md
"""
import argparse
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone

import feedparser
import httpx

from src.collectors.rss import USER_AGENT
from src.config import load_sources
from src.countries import country_name_ru
from src.engine.health import source_health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("diagnose")

# Human-readable explanations for each classified reason.
REASON_HELP = {
    "telegram_session": "Telegram-источник: collect.py его не трогает, собирает tg-collector. Все TG-источники мрут разом при протухшей сессии Telethon.",
    "never_collected": "Источник ни разу не отдал статью (last_at IS NULL) — DEAD с первого дня. Битый URL/неверный тип с момента добавления.",
    "dns_or_conn": "DNS не резолвится или соединение не устанавливается — домен мёртв/переехал или режется на сетевом уровне.",
    "timeout": "Таймаут ответа — сайт жив, но медленный/подвисает с датацентрового IP.",
    "geoblock_or_forbidden": "403/451 — гео-блок или бан датацентрового IP (типично для региональных сайтов).",
    "http_404_gone": "404/410 — фид удалён или URL изменился (сайт убрал RSS).",
    "rate_limited": "429 — нас лимитируют (часто Google-News site:-обёртки).",
    "http_5xx": "5xx — сервер источника отвечает ошибкой.",
    "http_4xx": "Прочая 4xx-ошибка запроса.",
    "empty_or_malformed_feed": "HTTP 200, но фид не парсится (bozo) и записей нет — это не RSS/Atom.",
    "empty_feed": "HTTP 200, валидный фид, но 0 записей — фид пустой.",
    "feed_ok_but_quiet": "Фид жив и отдаёт записи — источник просто давно молчит (или проблема дедупа/дат на нашей стороне).",
    "web_zero_extracted": "web-тип: страница достижима, но trafilatura/Firecrawl извлекли 0 статей (JS-сайт/пейволл/нестандартная вёрстка).",
    "web_reachable": "web-тип: страница отдаёт 200. Извлечение не проверялось (--deep для проверки).",
    "web_ok_but_quiet": "web-тип: извлечение работает — источник просто молчит.",
    "probe_error": "Непредвиденная ошибка при пробе.",
}


def _classify_http_status(code: int) -> str:
    if code in (403, 451):
        return "geoblock_or_forbidden"
    if code in (404, 410):
        return "http_404_gone"
    if code == 429:
        return "rate_limited"
    if 500 <= code < 600:
        return "http_5xx"
    return "http_4xx"


def probe(src: dict, deep: bool) -> tuple[str, str]:
    """Return (reason_code, detail) for one source."""
    stype = (src.get("type") or "").lower()
    url = src.get("url") or ""

    if stype == "telegram":
        return "telegram_session", "requires Telethon session (tg-collector)"

    # In DB mode a source that never produced an article is DEAD with last_at
    # NULL. In --yaml mode we have no ingestion history, so don't mislabel.
    if src.get("last_article_at") is None and src.get("status") == "DEAD":
        base_reason = "never_collected"
    else:
        base_reason = None

    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        return (base_reason or "dns_or_conn"), f"{type(e).__name__}: {str(e)[:120]}"
    except (httpx.ReadTimeout, httpx.PoolTimeout, httpx.TimeoutException) as e:
        return (base_reason or "timeout"), f"{type(e).__name__}: {str(e)[:120]}"
    except Exception as e:  # noqa: BLE001 — diagnostic must never crash on one source
        return (base_reason or "probe_error"), f"{type(e).__name__}: {str(e)[:120]}"

    if resp.status_code >= 400:
        reason = _classify_http_status(resp.status_code)
        return (base_reason or reason), f"HTTP {resp.status_code} ({reason})"

    # 200 OK — content-level classification.
    if stype == "rss":
        feed = feedparser.parse(resp.text)
        n = len(feed.entries)
        if feed.bozo and n == 0:
            exc = getattr(feed, "bozo_exception", "")
            return (base_reason or "empty_or_malformed_feed"), f"bozo: {str(exc)[:100]}"
        if n == 0:
            return (base_reason or "empty_feed"), "0 entries"
        if base_reason == "never_collected":
            return "never_collected", f"feed has {n} entries but none ingested (check dates/dedup)"
        return "feed_ok_but_quiet", f"{n} entries present"

    if stype == "web":
        if not deep:
            return (base_reason or "web_reachable"), f"HTTP 200, {len(resp.text)} bytes (use --deep to test extraction)"
        try:
            from src.collectors.scraper import scrape_web
            got = scrape_web(url, src.get("name", ""))
            if not got:
                return (base_reason or "web_zero_extracted"), "scrape_web returned 0 articles"
            if base_reason == "never_collected":
                return "never_collected", f"scrape_web returns {len(got)} but none ingested"
            return "web_ok_but_quiet", f"scrape_web returns {len(got)} articles"
        except Exception as e:  # noqa: BLE001
            return (base_reason or "web_zero_extracted"), f"scrape error: {str(e)[:100]}"

    return (base_reason or "probe_error"), f"unknown source_type={stype!r}"


def load_sources_from_yaml() -> list[dict]:
    """Source rows straight from the YAML catalogs — no DB needed.

    Lets the diagnostic run anywhere (e.g. cloud sessions that can't reach the
    prod DB) to find URL-level breakage in the *configured* feeds. There is no
    ingestion history here, so status is unknown ('?').
    """
    cfg = load_sources()
    rows = []
    for cc, data in cfg["countries"].items():
        for s in data.get("sources", []):
            rows.append({
                "source_id": None,
                "name": s.get("name", ""),
                "country_code": cc,
                "tier": s.get("tier", "mainstream"),
                "type": s.get("type"),
                "url": s.get("url", ""),
                "status": "?",
                "last_article_at": None,
            })
    return rows


def build_report(rows: list[dict], probe_all: bool, deep: bool) -> str:
    now = datetime.now(timezone.utc)

    targets = rows if probe_all else [r for r in rows if r["status"] in ("STALE", "DEAD")]
    logger.info("Probing %d/%d sources (%s)...", len(targets), len(rows),
                "all" if probe_all else "STALE+DEAD only")

    by_reason = Counter()
    by_reason_type = defaultdict(Counter)
    by_reason_tier = defaultdict(Counter)
    detailed = []

    for i, r in enumerate(targets, 1):
        reason, detail = probe(r, deep)
        by_reason[reason] += 1
        by_reason_type[reason][r.get("type") or "?"] += 1
        by_reason_tier[reason][r.get("tier") or "?"] += 1
        detailed.append({**r, "reason": reason, "detail": detail})
        logger.info("[%d/%d] %s/%s %-22s %-22s %s", i, len(targets),
                    r["country_code"], r.get("type"), r["status"], reason, r["name"][:40])

    # ── markdown ──
    lines = []
    lines.append(f"# Диагностика мёртвых источников — {now:%Y-%m-%d %H:%M UTC}\n")
    total = len(rows)
    dead = sum(1 for r in rows if r["status"] == "DEAD")
    stale = sum(1 for r in rows if r["status"] == "STALE")
    ok = sum(1 for r in rows if r["status"] == "OK")
    lines.append(f"Активных источников: **{total}** — OK {ok}, STALE {stale}, **DEAD {dead}**. "
                 f"Проб выполнено: {len(targets)} ({'все' if probe_all else 'STALE+DEAD'}; "
                 f"web-extraction {'включён' if deep else 'выключен'}).\n")

    lines.append("## Причины (по убыванию)\n")
    lines.append("| Причина | Кол-во | Что это значит |")
    lines.append("|---|---:|---|")
    for reason, cnt in by_reason.most_common():
        lines.append(f"| `{reason}` | {cnt} | {REASON_HELP.get(reason, '')} |")
    lines.append("")

    lines.append("## Причины × тип источника\n")
    lines.append("| Причина | " + " | ".join(sorted({t for c in by_reason_type.values() for t in c})) + " |")
    types = sorted({t for c in by_reason_type.values() for t in c})
    lines.append("|---|" + "|".join("---:" for _ in types) + "|")
    for reason, _ in by_reason.most_common():
        cells = " | ".join(str(by_reason_type[reason].get(t, 0)) for t in types)
        lines.append(f"| `{reason}` | {cells} |")
    lines.append("")

    # Per-country coverage (skipped in --yaml mode where statuses are unknown)
    if ok or stale or dead:
        by_country = defaultdict(Counter)
        for r in rows:
            by_country[r["country_code"]][r["status"].lower()] += 1
        lines.append("## Покрытие по странам (худшие сверху)\n")
        lines.append("| Страна | Всего | OK | STALE | DEAD | Покрытие |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        def cov(c):
            t = sum(c.values())
            return c["ok"] / t if t else 0
        for code, c in sorted(by_country.items(), key=lambda kv: cov(kv[1])):
            t = sum(c.values())
            lines.append(f"| {country_name_ru(code)} ({code}) | {t} | {c['ok']} | {c['stale']} | "
                         f"{c['dead']} | {cov(c)*100:.0f}% |")
        lines.append("")

    lines.append("## Детально (проблемные источники)\n")
    lines.append("| Страна | Источник | Тип | Тир | Статус | Причина | Деталь |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in sorted(detailed, key=lambda x: (x["reason"], x["country_code"])):
        if d["reason"] in ("feed_ok_but_quiet", "web_ok_but_quiet"):
            continue  # these are "alive, just quiet" — not failures
        name = (d["name"] or "")[:40].replace("|", "/")
        detail = (d["detail"] or "")[:80].replace("|", "/")
        lines.append(f"| {d['country_code']} | {name} | {d.get('type')} | {d.get('tier')} | "
                     f"{d['status']} | `{d['reason']}` | {detail} |")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Diagnose STALE/DEAD sources")
    parser.add_argument("--all", action="store_true", help="probe every source, not just STALE/DEAD")
    parser.add_argument("--deep", action="store_true", help="also test web extraction (slow)")
    parser.add_argument("--yaml", action="store_true",
                        help="read sources from YAML catalogs instead of the DB (no DB needed)")
    parser.add_argument("--out", default=f"docs/research/dead-sources-{datetime.now():%Y-%m-%d}.md",
                        help="markdown report path")
    args = parser.parse_args()

    if args.yaml:
        rows = load_sources_from_yaml()
        probe_all = True  # no health status to filter on
    else:
        rows = source_health()
        probe_all = args.all
    if not rows:
        logger.warning("No sources found.")
        return

    report = build_report(rows, probe_all=probe_all, deep=args.deep)
    with open(args.out, "w") as f:
        f.write(report)
    logger.info("Report written to %s", args.out)
    print("\n" + report)


if __name__ == "__main__":
    main()
