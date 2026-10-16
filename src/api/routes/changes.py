"""Unified, deduplicated public changes feed."""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from src.api.public_urls import safe_public_url
from src.api.routes.radar import SqlRadarReadService, _humanize_thesis
from src.api.signal_article_context import load_signal_article_previews
from src.countries import country_name_ru
from src.db import get_session
from src.engine.ru_index import LEVEL_RU, level_ru

router = APIRouter(prefix="/api/v2", tags=["changes"])
ChangeType = Literal["all", "index", "tone", "waves", "stories", "attention"]
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")
DATE_BUCKET = re.compile(r":(?:\d{4}-\d{2}(?:-\d{2})?|\d{8}|\d{6})$")
NOTABLE_EVENT_LABELS = {
    "военное": "Военное событие", "military": "Военное событие",
    "дипломатия": "Дипломатическое событие", "diplomatic": "Дипломатическое событие",
    "безопасность": "Событие в сфере безопасности", "security": "Событие в сфере безопасности",
    "экономика": "Экономическое событие", "economic": "Экономическое событие",
    "культура": "Событие в культуре", "cultural": "Событие в культуре",
}
NOTABLE_EVENT_FALLBACK = "Событие в отношениях с Россией"


def number(value: Any, *, signed: bool = False) -> str:
    result = f"{float(value):+.1f}" if signed else f"{float(value):.1f}"
    if float(result) == 0:
        return "0"
    return result.replace(".0", "").replace(".", ",").replace("-", "−")


def plural(count: int, forms: tuple[str, str, str]) -> str:
    return forms[2] if 11 <= count % 100 <= 14 else forms[0] if count % 10 == 1 else forms[1] if 2 <= count % 10 <= 4 else forms[2]


def russian_title(value: str | None, fallback: str) -> str:
    """Legacy fallback copy can contain an untranslated source headline."""
    return value.strip() if (value and re.search(r"[А-Яа-яЁё]", value)
                             and not re.search(r"[іїєґўәғқңөұүһјљњћђџ]", value, re.IGNORECASE)) else fallback


def clean_headline(value: str | None) -> str:
    headline = re.sub(r"^(?:(?:\((?:ВИДЕО|ФОТО|VIDEO|S\+)\)|BREAKING:)\s*)+",
                      "", (value or "").strip(), flags=re.IGNORECASE)
    separators = list(re.finditer(r"\s+(?:\||-|—)\s+", headline))
    if separators and len(headline[separators[-1].end():]) <= 40:
        headline = headline[:separators[-1].start()]
    return headline.strip()


def headline_identity(value: str | None) -> str:
    """Match source headlines, never the generic event-type display copy."""
    headline = "".join(c for c in clean_headline(value).casefold()
                       if c not in "\"'`„‚" and unicodedata.category(c) not in {"Pi", "Pf"})
    while headline and (headline[0].isspace() or unicodedata.category(headline[0]).startswith("P")):
        headline = headline[1:]
    while headline and (headline[-1].isspace() or unicodedata.category(headline[-1]).startswith("P")):
        headline = headline[:-1]
    headline = " ".join(headline.split())
    placeholders = {*NOTABLE_EVENT_LABELS.values(), NOTABLE_EVENT_FALLBACK, "Заметное событие"}
    return "" if headline in {label.casefold() for label in placeholders} else headline


def notable_event_copy(row: dict) -> tuple[str, str]:
    headline = clean_headline(row.get("title"))
    if russian_title(headline, ""):
        return headline, ""
    event_type = ((row.get("payload") or {}).get("event_type")
                  or (row.get("description") or "").partition(" · ")[2])
    title = NOTABLE_EVENT_LABELS.get(event_type.strip().casefold(), NOTABLE_EVENT_FALLBACK)
    if len(headline) > 110:
        excerpt = headline[:109]
        if not headline[109].isspace():
            excerpt = excerpt.rsplit(" ", 1)[0]
        headline = excerpt.rstrip(" .…") + "…"
    return title, f"«{headline}»" if headline else ""


def signal_copy(row: dict) -> tuple[str, str, str]:
    payload = row.get("payload") or {}
    kind = row["signal_type"]
    context = []
    direction = "attention"
    if kind == "index_shift":
        delta = payload.get("delta_24h")
        title = f"Индекс {number(delta, signed=True)} за сутки" if delta is not None else "Изменение индекса"
        if payload.get("score") is not None:
            context.append(f"теперь {number(payload['score'])}")
        if payload.get("level") in LEVEL_RU:
            context.append(level_ru(payload["level"]))
        direction = "better" if delta is not None and delta > 0 else "worse" if delta is not None and delta < 0 else "attention"
        if payload.get("needs_review"):
            title += " · требует проверки"
            direction = "attention"
    elif kind == "tone_shift":
        tone, mean, std = (payload.get(key) for key in ("tone", "mean_90d", "std"))
        title = f"Тон СМИ о России {number(tone, signed=True)}" if tone is not None else "Изменение тона СМИ о России"
        if mean is not None:
            usual = f"обычно {number(mean, signed=True)}"
            if std is not None:
                usual += f" ± {number(std)}"
            context.append(usual)
        z = payload.get("z_score")
        if z is not None:
            context.append("отклонение очень сильное" if abs(z) >= 3 else "отклонение сильное")
        delta = tone - mean if tone is not None and mean is not None else z
        direction = "better" if delta is not None and delta > 0 else "worse" if delta is not None and delta < 0 else "attention"
    elif kind in {"velocity_spike", "volume_surge"}:
        title = "Внимание к России выше обычного"
        count = payload.get("articles_24h", payload.get("volume"))
        if count is not None:
            count = int(count)
            context.append(f"{count} {plural(count, ('публикация', 'публикации', 'публикаций'))} за сутки")
        if payload.get("baseline_daily") is not None:
            context.append(f"обычно {number(payload['baseline_daily'])}")
    elif kind == "notable_event":
        title, original = notable_event_copy(row)
        if original:
            context.append(original)
    else:
        labels = {
            "tier_convergence": "Разные источники сообщают об одном событии",
            "official_silence": "Официальные СМИ не освещают событие",
            "sanctions_escalation": "Санкции против России ужесточились",
            "fx_move": "Заметное изменение курса к рублю",
        }
        title = labels.get(kind, "Изменение в отношениях с Россией")
    return title, ", ".join(context), direction


def collapse_signals(rows: list[dict]) -> list[dict]:
    """Merge consecutive UTC days by key or source headline, retaining evidence IDs."""
    by_identity: dict[tuple, list[dict]] = {}
    parents: dict[tuple, tuple] = {}

    def root(identity: tuple) -> tuple:
        parents.setdefault(identity, identity)
        while parents[identity] != identity:
            parents[identity] = parents[parents[identity]]
            identity = parents[identity]
        return parents[identity]

    for original in rows:
        row = {**original, "created_at": original["created_at"].astimezone(timezone.utc)}
        key = row.get("dedup_key") or str(row["id"])
        notable = row["signal_type"] == "notable_event"
        identity = ("notable_event", key.removeprefix("notable:")) if notable else (
            row["signal_type"], row.get("country_code"), DATE_BUCKET.sub("", key))
        headline = headline_identity(row.get("title")) if notable else ""
        if headline:
            parents[root(identity)] = root(("notable_headline", headline))
        by_identity.setdefault(identity, []).append(row)
    # A shared event key can bridge multiple source headlines; resolve aliases
    # after reading every row so grouping does not depend on input order.
    merged: dict[tuple, list[dict]] = {}
    for identity, group in by_identity.items():
        merged.setdefault(root(identity), []).extend(group)
    items = []
    for group in merged.values():
        runs: list[list[dict]] = []
        for row in sorted(group, key=lambda r: (r["created_at"], r["id"])):
            if not runs or (row["created_at"].date() - runs[-1][-1]["created_at"].date()).days > 1:
                runs.append([])
            runs[-1].append(row)
        for run in runs:
            latest = run[-1]
            title, context, direction = signal_copy(latest)
            days = len({r["created_at"].date() for r in run})
            if days > 1:
                first = run[0]["created_at"]
                repeat = f"держится {days} {plural(days, ('день', 'дня', 'дней'))}, с {first.day} {MONTHS[first.month - 1]}"
            elif len(run) == 2:
                repeat = "пришёл дважды"
            elif len(run) > 2:
                repeat = f"повторился {len(run)} {plural(len(run), ('раз', 'раза', 'раз'))}"
            else:
                repeat = ""
            codes = sorted({r["country_code"].strip() for r in run if r.get("country_code")})
            items.append({
                "id": f"signal:{latest['id']}", "kind": "signal", "type": latest["signal_type"],
                "country_codes": codes, "country_names": [country_name_ru(c) for c in codes],
                "title": title, "context": " · ".join(part for part in (context, repeat) if part),
                "direction": direction, "severity": latest["severity"],
                "occurred_at": latest["created_at"], "first_at": run[0]["created_at"],
                "repeat_count": len(run), "days_running": days, "href": f"/signals/{latest['id']}",
                "evidence_preview": [], "evidence_total": 0,
                "_signal_ids": [r["id"] for r in run], "_latest": latest,
            })
    return items


def matches(item: dict, type: ChangeType, country: str | None, important: bool) -> bool:
    category = "waves" if item["kind"] == "wave" else "stories" if item["kind"] == "story" else {
        "index_shift": "index", "tone_shift": "tone"}.get(item["type"], "attention")
    return (type in {"all", category} and (not country or country in item["country_codes"])
            and (not important or item["kind"] == "story" or item["severity"] in {"critical", "warning"}))


def encode_cursor(snapshot: datetime, binding: list, item: dict) -> str:
    data = [1, snapshot.isoformat(), binding, item["occurred_at"].isoformat(), item["id"]]
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def decode_cursor(cursor: str, binding: list) -> tuple[datetime, tuple[datetime, str]]:
    try:
        data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        version, snapshot, saved_binding, occurred_at, item_id = data
        snapshot, occurred_at = datetime.fromisoformat(snapshot), datetime.fromisoformat(occurred_at)
        if (version != 1 or binding != saved_binding or snapshot.tzinfo is None or occurred_at.tzinfo is None
                or occurred_at > snapshot or snapshot > datetime.now(timezone.utc)
                or not isinstance(item_id, str) or not re.fullmatch(r"(?:signal|story):[1-9]\d*|wave:[0-9a-f-]{36}", item_id)):
            raise ValueError
        return snapshot, (occurred_at, item_id)
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise HTTPException(422, "Некорректный курсор ленты изменений") from None


def paginate(items: list[dict], *, type: ChangeType, country: str | None, important: bool,
             snapshot: datetime, key: tuple | None, limit: int, binding: list | None = None) -> dict:
    filtered = sorted((item for item in items if matches(item, type, country, important)),
                      key=lambda item: (item["occurred_at"], item["id"]), reverse=True)
    raw_total = sum(item["repeat_count"] for item in filtered)
    remaining = [item for item in filtered if key is None or (item["occurred_at"], item["id"]) < key]
    page = remaining[:limit]
    return {"items": page, "total": len(filtered), "raw_total": raw_total,
            "next_cursor": encode_cursor(snapshot, binding or [type, country, important], page[-1]) if len(remaining) > limit else None}


def _article(row: dict) -> dict:
    return {"id": row["id"], "title": russian_title(row.get("title"), "Публикация по теме"),
            "url": safe_public_url(row.get("url")), "source_name": row.get("source_name") or "Источник",
            "published_at": row["published_at"].isoformat() if row.get("published_at") else None}


def load_evidence(session: Any, items: list[dict]) -> None:
    """One batch for the displayed page, deduplicated across every merged signal."""
    scopes = []
    for item in items:
        if item["kind"] == "signal":
            scopes.extend({"key": item["id"], "signal_id": sid, "story_id": None, "wave_id": None} for sid in item["_signal_ids"])
        else:
            scopes.append({"key": item["id"], "signal_id": None,
                           "story_id": item.get("_story_id"), "wave_id": item.get("_wave_id")})
    if not scopes:
        return
    rows = session.execute(text("""
        WITH requested AS (
          SELECT * FROM jsonb_to_recordset(CAST(:scopes AS jsonb))
            AS r(key text, signal_id integer, story_id bigint, wave_id bigint)
        ), candidates AS (
          SELECT r.key, unnest(se.article_ids) AS article_id
          FROM requested r JOIN signal_evidence se ON se.signal_id = r.signal_id
          UNION
          SELECT r.key, sa.article_id FROM requested r JOIN story_articles sa ON sa.story_id = r.story_id
          UNION
          SELECT r.key, COALESCE(e.article_id, o.article_id)
          FROM requested r JOIN radar_trend_evidence e ON (
            e.trend_id = r.wave_id OR EXISTS (
              SELECT 1 FROM radar_trend_members m WHERE m.meta_trend_id = r.wave_id
                AND m.country_trend_id = e.trend_id AND m.left_at IS NULL))
          LEFT JOIN radar_observations o ON o.id = e.observation_id
          WHERE e.evidence->>'_relation_only' IS DISTINCT FROM 'true'
        ), unique_articles AS (
          SELECT DISTINCT ON (c.key, COALESCE(NULLIF(a.resolved_url, ''), a.url, a.id::text))
            c.key, a.id, a.title, COALESCE(NULLIF(a.resolved_url, ''), a.url) AS url,
            a.published_at, s.name AS source_name
          FROM candidates c JOIN articles a ON a.id = c.article_id
          LEFT JOIN article_country_facts s ON s.article_id = a.id
          ORDER BY c.key, COALESCE(NULLIF(a.resolved_url, ''), a.url, a.id::text), a.published_at DESC, a.id DESC
        ), ranked AS (
          SELECT *, COUNT(*) OVER (PARTITION BY key) AS total,
            ROW_NUMBER() OVER (PARTITION BY key ORDER BY published_at DESC NULLS LAST, id DESC) AS rank
          FROM unique_articles
        ) SELECT * FROM ranked WHERE rank <= 2 ORDER BY key, rank
    """), {"scopes": json.dumps(scopes)}).mappings().all()
    by_id = {item["id"]: item for item in items}
    for row in rows:
        item = by_id[row["key"]]
        item["evidence_total"] = row["total"]
        item["evidence_preview"].append(_article(dict(row)))
    # For aggregate signals without article evidence, retain the existing nearby
    # publication context. This is context, never a claim of causation.
    fallback = [item for item in items if item["kind"] == "signal" and not item["evidence_total"]]
    if fallback:
        from types import SimpleNamespace
        previews = load_signal_article_previews(session, [SimpleNamespace(id=item["_latest"]["id"]) for item in fallback])
        for item in fallback:
            preview = previews.get(item["_latest"]["id"], {})
            item["evidence_total"] = preview.get("total", 0)
            item["evidence_preview"] = [{**a, "title": russian_title(a.get("title"), "Публикация по теме")} for a in preview.get("articles", [])[:2]]


@router.get("/changes")
def get_changes(days: int = Query(7, ge=1, le=30), type: ChangeType = "all",
                country: str | None = Query(None, pattern=r"^[A-Za-z]{2}$"), important: bool = False,
                cursor: str | None = Query(None, max_length=2048), limit: int = Query(30, ge=1, le=100)):
    country = country.upper() if country else None
    binding = [type, country, important, days]
    # Days are bound separately from the public filters to reject cross-window cursors.
    snapshot, key = decode_cursor(cursor, binding) if cursor else (datetime.now(timezone.utc), None)
    params = {"start": snapshot - timedelta(days=days), "end": snapshot}
    with get_session() as session:
        rows = session.execute(text("""
            SELECT id, signal_type, country_code, severity, title, description, payload, dedup_key, created_at
            FROM signals WHERE created_at >= :start AND created_at <= :end
        """), params).mappings().all()
        items = collapse_signals([dict(row) for row in rows])
        stories = session.execute(text("""
            SELECT st.*, ARRAY(SELECT TRIM(sc.country_code) FROM story_countries sc
                               WHERE sc.story_id = st.id ORDER BY sc.country_code) AS countries
            FROM stories st WHERE st.created_at >= :start AND st.created_at <= :end
              AND NOT (st.meta ?| ARRAY['merged_into_story_id', 'quarantined_at', 'canary_quarantined_at'])
              AND st.country_count >= 2
        """), params).mappings().all()
        for row in stories:
            codes = row["countries"]
            count = row["article_count"]
            items.append({"id": f"story:{row['id']}", "kind": "story", "type": "story",
                          "country_codes": codes, "country_names": [country_name_ru(c) for c in codes],
                          "title": russian_title(row["title_ru"], "Новый сюжет об отношениях с Россией"),
                          "context": f"{', '.join(country_name_ru(c) for c in codes)} · {count} {plural(count, ('публикация', 'публикации', 'публикаций'))}",
                          "direction": "new", "severity": "info", "occurred_at": row["created_at"],
                          "first_at": row["created_at"], "repeat_count": 1, "days_running": 1,
                          "href": f"/stories/{row['id']}", "evidence_preview": [], "evidence_total": 0, "_story_id": row["id"]})
        waves = session.execute(text(f"""
            SELECT trend.*, {SqlRadarReadService._wave_json('trend.id')} AS country_waves
            FROM radar_trends trend
            WHERE {SqlRadarReadService._public_meta_gate_sql('trend')}
              AND trend.state IN ('emerging', 'confirmed', 'cooling')
              AND trend.updated_at >= :start AND trend.updated_at <= :end
        """), params).mappings().all()
        for row in waves:
            codes = sorted({w["country_code"].strip() for w in row["country_waves"]})
            title = _humanize_thesis(row["subject_key"], row["direction"], row["title_ru"])
            items.append({"id": f"wave:{row['public_id']}", "kind": "wave", "type": "wave",
                          "country_codes": codes, "country_names": [country_name_ru(c) for c in codes],
                          "title": russian_title(title, "Изменение в нескольких странах"),
                          "context": ", ".join(country_name_ru(c) for c in codes), "direction": "attention",
                          "severity": "info", "occurred_at": row["updated_at"], "first_at": row["first_observed_at"],
                          "repeat_count": 1, "days_running": 1, "href": f"/radar/{row['public_id']}",
                          "evidence_preview": [], "evidence_total": 0, "_wave_id": row["id"]})
        response = paginate(items, type=type, country=country, important=important, snapshot=snapshot, key=key, limit=limit, binding=binding)
        load_evidence(session, response["items"])
    response["items"] = [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in item.items() if not k.startswith("_")} for item in response["items"]]
    return response
