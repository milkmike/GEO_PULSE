"""Changes feed grouping, public copy, filters and cursor contract."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.changes import (
    collapse_signals, decode_cursor, encode_cursor, matches, number, paginate, router, signal_copy,
)
from src.engine import signals

NOW = datetime(2026, 10, 16, 0, tzinfo=timezone.utc)


def signal(id=1, day=16, kind="tone_shift", country="GB", key=None, **kwargs):
    return dict(id=id, signal_type=kind, country_code=country, severity="warning",
                title="Заметное событие", payload={}, created_at=NOW.replace(day=day),
                dedup_key=key or f"{kind}:{country}:202610{day:02}", **kwargs)


def test_consecutive_days_and_same_day_repeats_merge_with_latest_copy():
    rows = [signal(3, 16), signal(1, 14), signal(2, 15), signal(4, 16)]
    rows[0]["payload"] = {"tone": -12.7}
    rows[-1]["payload"] = {"tone": -13}
    [item] = collapse_signals(rows)
    assert item["days_running"] == 3
    assert item["repeat_count"] == 4
    assert item["first_at"].day == 14 and item["occurred_at"].day == 16
    assert item["title"] == "Тон СМИ о России −13"
    assert "держится 3 дня, с 14 октября" in item["context"]
    assert item["_signal_ids"] == [1, 2, 3, 4]


def test_gap_in_days_starts_a_separate_run():
    items = collapse_signals([signal(1, 12), signal(2, 14), signal(3, 15)])
    assert [(i["repeat_count"], i["days_running"]) for i in items] == [(1, 1), (2, 2)]


def test_notable_event_identity_is_event_key_across_countries():
    items = collapse_signals([
        signal(1, 15, "notable_event", "GB", "notable:sanctions:2026"),
        signal(2, 16, "notable_event", "US", "notable:sanctions:2026"),
        signal(3, 16, "notable_event", "US", "notable:other"),
    ])
    assert len(items) == 2
    assert items[0]["repeat_count"] == 2
    assert items[0]["country_codes"] == ["GB", "US"]


def test_notable_event_gap_starts_a_separate_run():
    assert len(collapse_signals([
        signal(1, 12, "notable_event", key="notable:event"),
        signal(2, 16, "notable_event", key="notable:event"),
    ])) == 2


def test_notable_events_merge_cleaned_source_headlines_across_keys_and_countries():
    headline = "Глава МИД Кыргызстана подтвердил принудительную вербовку"
    rows = [
        {**signal(1, kind="notable_event", country="KZ", key="notable:kazakhstan"),
         "title": f"(ВИДЕО) «{headline}» | Газета", "created_at": NOW.replace(hour=20, minute=52)},
        {**signal(2, kind="notable_event", country="TJ", key="notable:tajikistan"),
         "title": f'  BREAKING: "{headline.upper().replace(" ", "  ")}"… — Новости',
         "created_at": NOW.replace(hour=18, minute=55)},
        {**signal(3, 15, "notable_event", "KZ", "notable:kazakhstan"), "title": headline},
    ]
    [item] = collapse_signals(rows)
    assert item["country_codes"] == ["KZ", "TJ"]
    assert item["country_names"] == ["Казахстан", "Таджикистан"]
    assert item["repeat_count"] == 3
    assert item["_signal_ids"] == [3, 2, 1]
    assert item["id"] == "signal:1" and item["occurred_at"] == rows[0]["created_at"]
    assert matches(item, "attention", "TJ", False)


@pytest.mark.parametrize("quote_pair", [("«", "»"), ('"', '"'), ("„", "“"), ("‘", "’"), ("‚", "‘")])
def test_notable_headline_normalizes_quotes_inside_the_title(quote_pair):
    opening, closing = quote_pair
    rows = [
        {**signal(1, kind="notable_event", key="notable:1"), "title": "МИД подтвердил событие"},
        {**signal(2, kind="notable_event", key="notable:2"),
         "title": f"{opening}МИД{closing} подтвердил событие"},
    ]
    assert len(collapse_signals(rows)) == 1


@pytest.mark.parametrize("headline", ["", "Военное событие", "Дипломатическое событие",
                                     "Событие в сфере безопасности", "Экономическое событие",
                                     "Событие в культуре", "Событие в отношениях с Россией"])
def test_notable_placeholder_headlines_do_not_merge_different_keys(headline):
    rows = [{**signal(i, kind="notable_event", key=f"notable:{i}"), "title": headline} for i in (1, 2)]
    assert len(collapse_signals(rows)) == 2


def test_notable_source_headlines_do_not_merge_by_display_fallback_or_excerpt():
    prefix = "Russia " * 20
    rows = [{**signal(i, kind="notable_event", key=f"notable:{i}"),
             "title": prefix + ending, "payload": {"event_type": "military"}}
            for i, ending in enumerate(("negotiations begin", "negotiations end"), start=1)]
    items = collapse_signals(rows)
    assert len(items) == 2
    assert items[0]["title"] == items[1]["title"] == "Военное событие"
    assert items[0]["context"] == items[1]["context"]


def test_notable_headline_aliases_preserve_key_merges_regardless_of_input_order():
    rows = [
        {**signal(1, kind="notable_event", key="notable:first"), "title": "Переговоры начались"},
        {**signal(2, kind="notable_event", key="notable:second"), "title": "Обсуждение договора"},
        {**signal(3, kind="notable_event", key="notable:first"), "title": "Обсуждение договора"},
    ]
    for ordered in (rows, rows[::-1]):
        [item] = collapse_signals(ordered)
        assert item["repeat_count"] == 3 and item["_signal_ids"] == [1, 2, 3]


def test_notable_matching_headlines_still_respect_gaps_in_days():
    rows = [{**signal(i, day, "notable_event", key=f"notable:{i}"), "title": "Переговоры начались"}
            for i, day in ((1, 12), (2, 16))]
    assert len(collapse_signals(rows)) == 2


@pytest.mark.parametrize("bucket", ["2026-10-15", "20261015", "2026-10", "202610"])
def test_date_buckets_removed_without_losing_type_or_country(bucket):
    items = collapse_signals([signal(1, 15, key=f"tone_shift:GB:{bucket}"), signal(2, 16)])
    assert len(items) == 1
    assert len(collapse_signals([signal(1), signal(2, country="US"), signal(3, kind="index_shift")])) == 3


def test_same_day_twice_and_singular_repeat_copy():
    [item] = collapse_signals([signal(1), signal(2)])
    assert item["days_running"] == 1 and "пришёл дважды" in item["context"]


@pytest.mark.parametrize("kind,payload,title,context,direction", [
    ("index_shift", {"delta_24h": -9.6, "score": -54, "level": "tension"}, "Индекс −9,6 за сутки", "теперь −54, напряжение", "worse"),
    ("tone_shift", {"tone": -12.7, "mean_90d": -2.1, "std": 2.6, "z_score": -4}, "Тон СМИ о России −12,7", "обычно −2,1 ± 2,6, отклонение очень сильное", "worse"),
    ("tone_shift", {"tone": -1, "mean_90d": -3}, "Тон СМИ о России −1", "обычно −3", "better"),
    ("tone_shift", {"tone": -1, "mean_90d": 0.04, "std": 0.3}, "Тон СМИ о России −1", "обычно 0 ± 0,3", "worse"),
    ("velocity_spike", {"articles_24h": 21, "baseline_daily": 3}, "Внимание к России выше обычного", "21 публикация за сутки, обычно 3", "attention"),
    ("volume_surge", {"volume": 24, "ratio": 3}, "Внимание к России выше обычного", "24 публикации за сутки", "attention"),
    ("index_shift", {"delta_24h": -41.6, "needs_review": True}, "Индекс −41,6 за сутки · требует проверки", "", "attention"),
])
def test_signal_copy(kind, payload, title, context, direction):
    row = signal(kind=kind)
    row["payload"] = payload
    assert signal_copy(row) == (title, context, direction)


@pytest.mark.parametrize("value", [0, 0.04, -0.04, -0.0])
@pytest.mark.parametrize("signed", [False, True])
def test_rounded_zero_has_no_sign(value, signed):
    assert number(value, signed=signed) == "0"


@pytest.mark.parametrize("value,expected", [(0.06, "+0,1"), (-0.06, "−0,1"), (2, "+2")])
def test_nonzero_numbers_keep_their_sign(value, expected):
    assert number(value, signed=True) == expected


@pytest.mark.parametrize("headline,description,payload,title,context", [
    ("BREAKING: Russia Has Struck NATO Territory After Drone Hits Tanker Off Romania",
     "Болгария · военное", {}, "Военное событие",
     "«Russia Has Struck NATO Territory After Drone Hits Tanker Off Romania»"),
    ("Japan denies spying after Russia arrests journalist - The Japan Times",
     "Япония · безопасность", {"event_type": "security"}, "Событие в сфере безопасности",
     "«Japan denies spying after Russia arrests journalist»"),
    ("Великобритания поставит Украине новые партии Storm Shadows | Caliber.Az",
     "Азербайджан · военное", {}, "Великобритания поставит Украине новые партии Storm Shadows", ""),
    ("(ВИДЕО) Гросу разберется с Yandex Go?/ Додон поехал в Россию…",
     "Молдова · дипломатия", {}, "Гросу разберется с Yandex Go?/ Додон поехал в Россию…", ""),
    ("(S+) BREAKING: Новые переговоры — Газета", "", {}, "Новые переговоры", ""),
    ("Переговоры - Новое соглашение | Газета", "", {}, "Переговоры - Новое соглашение", ""),
    ("(ВІДЕО) Україна і Росія | Газета", "", {}, "Событие в отношениях с Россией",
     "«(ВІДЕО) Україна і Росія»"),
])
def test_notable_event_headlines(headline, description, payload, title, context):
    row = {**signal(kind="notable_event"), "title": headline, "description": description, "payload": payload}
    assert signal_copy(row) == (title, context, "attention")


@pytest.mark.parametrize("event_type,title", [
    ("военное", "Военное событие"), ("military", "Военное событие"),
    ("дипломатия", "Дипломатическое событие"), ("diplomatic", "Дипломатическое событие"),
    ("безопасность", "Событие в сфере безопасности"), ("security", "Событие в сфере безопасности"),
    ("экономика", "Экономическое событие"), ("economic", "Экономическое событие"),
    ("культура", "Событие в культуре"), ("cultural", "Событие в культуре"),
    ("unknown", "Событие в отношениях с Россией"),
])
@pytest.mark.parametrize("from_description", [False, True])
def test_notable_event_type_fallback(event_type, title, from_description):
    row = {**signal(kind="notable_event"), "title": "Source headline",
           "payload": {} if from_description else {"event_type": event_type},
           "description": f"Япония · {event_type}" if from_description else "Япония · военное"}
    assert signal_copy(row) == (title, "«Source headline»", "attention")


def test_notable_original_is_shortened_at_a_word_boundary():
    headline = "Russia " * 15 + "negotiations continue tomorrow"
    row = {**signal(kind="notable_event"), "title": f"BREAKING: {headline} | News"}
    assert signal_copy(row)[1] == f"«{('Russia ' * 15).strip()}…»"


@pytest.mark.parametrize("tail_length", [40, 41])
def test_notable_headline_only_removes_short_publisher_suffix(tail_length):
    headline = "Новые переговоры"
    row = {**signal(kind="notable_event"), "title": f"{headline} — {'x' * tail_length}"}
    assert signal_copy(row)[0] == (headline if tail_length == 40 else row["title"])


def test_missing_payload_and_untranslated_notable_title_have_russian_fallbacks():
    for kind in ("index_shift", "tone_shift", "volume_surge", "notable_event", "unknown"):
        row = signal(kind=kind)
        row["title"] = "Breaking news"
        title, context, _ = signal_copy(row)
        assert "Breaking" not in title
        assert "None" not in title + context


def test_filters_and_raw_total_are_applied_to_merged_items():
    items = collapse_signals([signal(1), signal(2), signal(3, kind="index_shift")])
    story = {**items[0], "id": "story:1", "kind": "story", "repeat_count": 1, "severity": "info"}
    wave = {**story, "id": "wave:1", "kind": "wave"}
    assert matches(story, "stories", "GB", True)
    assert not matches(wave, "waves", None, True)
    assert matches(wave, "waves", None, False)
    assert not matches(story, "stories", "US", False)
    assert matches({**items[0], "type": "notable_event"}, "attention", None, False)
    page = paginate(items, type="tone", country="GB", important=True, snapshot=NOW, key=None, limit=30)
    assert page["total"] == 1 and page["raw_total"] == 2


def test_cursor_paginates_timestamp_ties_and_keeps_snapshot_and_filter_binding():
    items = collapse_signals([signal(1), signal(2, country="US"), signal(3, kind="index_shift")])
    binding = ["all", None, False]
    page = paginate(items, type="all", country=None, important=False, snapshot=NOW, key=None, limit=1)
    snapshot, key = decode_cursor(page["next_cursor"], binding)
    rest = paginate(items, type="all", country=None, important=False, snapshot=snapshot, key=key, limit=30)
    assert len(rest["items"]) == 2 and rest["next_cursor"] is None
    assert page["items"][0]["id"] not in {i["id"] for i in rest["items"]}
    assert snapshot == NOW
    for wrong in (["tone", None, False], ["all", "US", False], ["all", None, True], ["all", None, False, 30]):
        with pytest.raises(Exception) as error:
            decode_cursor(page["next_cursor"], wrong)
        assert error.value.status_code == 422


@pytest.mark.parametrize("cursor", ["!", "e30", "bnVsbA", "W10", "____"])
def test_bad_cursors_rejected(cursor):
    with pytest.raises(Exception) as error:
        decode_cursor(cursor, [])
    assert error.value.status_code == 422


@pytest.mark.parametrize("query", ["days=0", "days=31", "limit=101", "limit=0", "type=other", "country=USA", "important=wrong", "cursor=bad"])
def test_endpoint_validation_does_not_reach_database(query):
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get(f"/api/v2/changes?{query}").status_code == 422


@pytest.mark.parametrize("delta,severity,review", [(-41.6, "info", True), (19, "info", True), (18, "critical", False), (-14, "critical", False), (7, "warning", False)])
def test_index_detector_retains_extreme_moves_for_review(monkeypatch, delta, severity, review):
    row = SimpleNamespace(country_code="BA", delta_24h=delta, score=-54, level="tension", time=NOW,
                          baseline_time=NOW - timedelta(days=1), baseline_score=-12.4, baseline_level="neutral")
    session = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: [row]))
    calls = []
    monkeypatch.setattr(signals, "_emit", lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    assert signals.detect_index_shifts(session) == 1
    args, kwargs = calls[0]
    assert kwargs["severity"] == severity
    assert bool(kwargs["payload"].get("needs_review")) == review
    assert ("требует проверки" in kwargs["title"]) == review
    assert kwargs["evidence"].threshold["absolute_delta_sanity_max"] == 18


def test_http_feed_merges_sources_and_hydrates_only_the_displayed_page(monkeypatch):
    from contextlib import contextmanager
    from src.api.routes import changes

    calls = []
    class Result:
        def __init__(self, rows):
            self.rows = rows
        def mappings(self):
            return self
        def all(self):
            return self.rows

    class Session:
        def execute(self, sql, params):
            sql = str(sql)
            calls.append((sql, params))
            if "FROM signals WHERE" in sql:
                return Result([signal(1, 15), signal(2, 16)])
            if "FROM stories st WHERE" in sql:
                return Result([dict(id=50, title_ru="Новый договор", countries=["GB", "JP"], article_count=4, created_at=NOW)])
            if "FROM radar_trends trend" in sql:
                return Result([])
            if "jsonb_to_recordset" in sql:
                return Result([dict(key=key, id=1, title="Соглашение", url="https://example.org/1", source_name="Газета", published_at=NOW, total=4)
                               for key in ("signal:2", "story:50") if key in params["scopes"]])
            raise AssertionError(sql)

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(changes, "get_session", factory)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get("/api/v2/changes?limit=1")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2 and page["raw_total"] == 3
    assert page["items"][0]["kind"] == "story"
    assert page["items"][0]["evidence_total"] == 4
    assert all(not key.startswith("_") for key in page["items"][0])
    assert len(calls) == 4
    first_snapshot = calls[0][1]["end"]
    rest = client.get("/api/v2/changes", params={"cursor": page["next_cursor"]}).json()
    assert rest["items"][0]["id"] == "signal:2"
    assert rest["next_cursor"] is None
    assert rest["items"][0]["repeat_count"] == 2
    assert calls[4][1]["end"] == first_snapshot
    assert "expires_at" not in calls[0][0]
