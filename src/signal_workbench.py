"""Offline early-signal review. Never imports a transport or writes production data."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from html import escape
import json
from zoneinfo import ZoneInfo

from src.monitoring_registry import MONITORING_COUNTRIES as COUNTRIES
from src.early_signals import encode, prepare_payload, parse_response, select_candidates, source_key
from src.signal_hypotheses import prepare_prompt, validate_dossier


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be an ISO timestamp with timezone")
    return value.astimezone(timezone.utc)


def request_hash(payload):
    return hashlib.sha256(encode(payload)).hexdigest()


def article_snapshot(article, *, screened=False):
    fields = ("id", "title", "excerpt", "source_id", "source_name", "country_code", "url",
              "published_at", "collected_at")
    result = {key: article[key] for key in fields}
    for key in ("published_at", "collected_at"):
        result[key] = instant(result[key]).isoformat()
    if screened:
        result["excerpt"] = result["excerpt"][:2000]
    return result


def prepare_review(articles, *, as_of, limit=80):
    """Produce requests only. Caller must explicitly import externally obtained answers."""
    now = instant(as_of)
    if not isinstance(articles, list):
        raise ValueError("articles must be a list")
    candidates = [article_snapshot(a, screened=True) for a in select_candidates(articles, limit=limit, as_of=now)]
    codes = sorted(code for code in COUNTRIES if code != "RU")
    pending = candidates[:]
    batches = []
    while pending:
        payload, selected = prepare_payload(pending, codes)
        if not selected:
            raise ValueError("screening could not prepare an article")
        batches.append({"request_hash": request_hash(payload), "payload": payload,
                       "articles": selected, "snapshot_hash": request_hash(selected)})
        ids = {a["id"] for a in selected}
        pending = [a for a in pending if a["id"] not in ids]
    return {"version": 1, "mode": "offline", "as_of": now.isoformat(),
            "input_count": len(articles), "selected_count": len(candidates),
            "country_codes": codes, "batches": batches,
            "note": "Requests prepared, not executed. No model accuracy measured."}


def import_screening(prepared, responses):
    if not isinstance(responses, list):
        raise ValueError("responses must be a list of request_hash/data envelopes")
    pending = {}
    for batch in prepared["batches"]:
        payload, selected = prepare_payload(batch["articles"], prepared["country_codes"])
        key = request_hash(payload)
        if (key != batch["request_hash"] or payload != batch["payload"]
                or request_hash(batch["articles"]) != batch["snapshot_hash"]
                or len(selected) != len(batch["articles"]) or key in pending):
            raise ValueError("prepared source snapshot or payload changed")
        pending[key] = selected
    records, seen = [], set()
    for response in responses:
        if not isinstance(response, dict) or set(response) != {"request_hash", "data"}:
            raise ValueError("invalid response envelope")
        key = response["request_hash"]
        if not isinstance(key, str) or key not in pending or key in seen:
            raise ValueError("response does not match one unique prepared request")
        parsed, _ = parse_response(response["data"], pending[key], prepared["country_codes"])
        for record in parsed:
            record["snapshot_hash"] = request_hash(record["article"])
        records.extend(parsed)
        seen.add(key)
    return {"mode": "offline", "as_of": prepared["as_of"], "records": records,
            "selected_count": prepared["selected_count"], "screened_count": len(records),
            "unanswered_batches": len(pending) - len(seen),
            "note": "Imported model labels are candidates, not verified facts."}


def assemble_context(screened, article_id, context):
    if type(article_id) is not int or not isinstance(context, list):
        raise ValueError("invalid context selection")
    record = next((r for r in screened["records"] if r["article"]["id"] == article_id), None)
    if record is None or record["classification"]["signal"] == "routine":
        raise ValueError("choose a screened change or uncertain candidate")
    if (record["source_key"] != source_key(record["article"])
            or record.get("snapshot_hash") != request_hash(record["article"])):
        raise ValueError("screened article changed")
    articles = [article_snapshot(record["article"])] + [article_snapshot(a) for a in context]
    prompt = prepare_prompt(articles, as_of=instant(screened["as_of"]))
    return {"as_of": screened["as_of"], "articles": articles,
            "screening": record["classification"], "prompt": prompt,
            "note": "Context was selected explicitly; shared entities do not prove a causal link."}


def review_draft(context, draft, *, example=False):
    dossier = validate_dossier(draft, context["articles"], as_of=instant(context["as_of"]))
    return dossier, render_dossier(dossier, context["articles"], example=example or context.get("example") is True)


def render_dossier(dossier, evidence, *, example=False):
    """Render only a validated, explicitly provisional dossier; escape every source value."""
    d = validate_dossier({key: value for key, value in dossier.items() if key in {
        "headline_ru", "observations", "interpretation_ru", "hypothesis_ru", "opportunity_ru",
        "russia_basis", "counterargument_ru", "watch", "country_codes", "horizon_date",
    }}, evidence, as_of=instant(dossier["as_of"]))
    esc = lambda value: escape(str(value), quote=True)
    date_time = lambda value: instant(value).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y, %H:%M МСК")
    sources = {a["id"]: a for a in evidence}
    observed = []
    for index, observation in enumerate(d["observations"], 1):
        a = sources[observation["article_id"]]
        # Validate against the same URL policy before including a clickable link.
        from src.api.public_urls import safe_public_url
        href = safe_public_url(a["url"])
        link = (f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">Открыть публикацию ↗</a>'
                if href and not example else '<span>Учебный источник — ссылка не открывается</span>' if example
                else '<span>Ссылка недоступна</span>')
        observed.append(f'''<li><p>{esc(observation['text_ru'])}</p><details>
          <summary>Источник {index} · {esc(a['source_name'])}</summary>
          <p class="source-title">{esc(a['title'])}</p><blockquote>{esc(observation['quote'])}</blockquote>
          <p class="meta">Опубликовано: {esc(date_time(a['published_at']))}<br>Получено: {esc(date_time(a['collected_at']))}</p>{link}</details></li>''')
    triggers = ''.join(f'''<li><span class="tag">{'Усилит гипотезу' if t['effect'] == 'strengthens' else 'Ослабит гипотезу'}</span>
        <p>{esc(t['observation_ru'])}</p><small>Проверить до {esc(t['by_date'])}</small></li>''' for t in d['watch'])
    opportunity = (f'<section><h2>Где может появиться возможность</h2><p>{esc(d["opportunity_ru"])}</p></section>'
                   if d['opportunity_ru'] else '<p class="notice">Связь с российскими участниками пока не установлена.</p>')
    location = ' · '.join(COUNTRIES.get(c, {}).get('name_ru', c) for c in d['country_codes']) or 'Страна требует уточнения'
    banner = ('ВЫМЫШЛЕННЫЙ ПРИМЕР · события и источники созданы для проверки формата'
              if example else 'ЧЕРНОВИК ДЛЯ АНАЛИТИКА · выводы и связь между событиями требуют проверки')
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
      <title>{esc(d['headline_ru'])} — Массаракш</title><style>
      :root{{color-scheme:dark;--bg:#0b1016;--fg:#efede5;--muted:#adb8c6;--line:#33404e;--accent:#a9caff}}
      *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 system-ui,sans-serif}}
      header,main,footer{{max-width:1120px;margin:auto;padding:28px 36px}}header{{border-bottom:1px solid var(--line);display:flex;gap:20px;justify-content:space-between;flex-wrap:wrap}}
      .brand{{letter-spacing:.16em;font-weight:700}}.meta,small,.notice{{color:var(--muted)}}.banner{{border-left:3px solid #d5ae61;padding:10px 18px;margin:0 0 36px;background:#171c23;font-size:13px}}
      h1{{font:clamp(30px,4vw,48px)/1.15 Georgia,serif;max-width:880px;letter-spacing:-.015em;margin:20px 0 30px}}h2{{font-size:19px;line-height:1.3;margin:0 0 14px}}p{{margin:0 0 16px;overflow-wrap:anywhere}}
      .layout{{display:grid;grid-template-columns:minmax(0,2fr) minmax(230px,1fr);gap:64px}}section{{padding:26px 0;border-top:1px solid var(--line)}}.lead{{font-size:21px;line-height:1.55}}
      aside{{padding-left:28px;border-left:1px solid var(--line)}}ol,ul{{list-style:none;margin:0;padding:0}}li{{padding:0 0 24px}}.facts li+li{{padding-top:20px;border-top:1px solid var(--line)}}
      summary,a{{color:var(--accent);cursor:pointer;min-height:44px;padding:10px 0;display:inline-block}}summary{{display:list-item}}details{{font-size:14px;color:var(--muted)}}blockquote{{border-left:2px solid var(--line);margin:16px 0;padding-left:16px}}a:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:5px}}.source-title{{color:var(--fg)}}.tag{{font-size:12px;color:var(--accent);display:block;margin-bottom:8px}}footer{{border-top:1px solid var(--line);font-size:13px;color:var(--muted)}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
      @media(max-width:760px){{header,main,footer{{padding:22px}}.layout{{grid-template-columns:1fr;gap:16px}}aside{{padding-left:0;border-left:0}}.lead{{font-size:19px}}h1{{margin:18px 0 24px}}}}
      </style></head><body><header><span class="brand">МАССАРАКШ</span><span class="meta">Ранние сигналы · редакторский просмотр</span></header>
      <main><p class="banner">{banner}</p><p class="meta">{esc(location)} · горизонт до {esc(d['horizon_date'])}<br>По материалам на {esc(date_time(d['as_of']))}</p><h1>{esc(d['headline_ru'])}</h1>
      <div class="layout"><div><section><h2>Что заметили</h2><ol class="facts">{''.join(observed)}</ol></section>
      <section><h2>Как это может быть связано</h2><p>{esc(d['interpretation_ru'])}</p></section>
      <section><h2>Рабочая гипотеза</h2><p class="lead">{esc(d['hypothesis_ru'])}</p></section>{opportunity}</div>
      <aside><section><h2>Что говорит против</h2><p>{esc(d['counterargument_ru'])}</p></section><section><h2>За чем следить</h2><ul>{triggers}</ul></section></aside></div></main>
      <footer>Цитаты сверены с переданными текстами. Достоверность сообщений, смысл пересказа и обоснованность гипотезы требуют проверки аналитиком. Повторные публикации не считаются независимым подтверждением.
      <details><summary>Сохранённые материалы разбора</summary><pre>{esc(json.dumps(dict(d, example=bool(example)), ensure_ascii=False, indent=2))}</pre></details></footer></body></html>'''
