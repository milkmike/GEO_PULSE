"""Discovery leads stay visible without becoming reviewed analytical claims."""
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def row(**changes):
    return dict(article_id=1, title="Serbia considers sanctions on Russia",
                body_excerpt="Serbia considers sanctions on Russia",
                source_title="Serbia considers sanctions on Russia",
                source_excerpt="Serbia considers sanctions on Russia",
                title_ru="Сербия рассматривает санкции против России",
                published_at=NOW-timedelta(hours=2), collected_at=NOW-timedelta(hours=1),
                publisher_name="Example", publisher_country_code="US",
                url="https://example.com/news", classification=dict(
                    countries=["RS"], country_primary="RS", russia_relation="uncertain", topic="sanctions",
                    event_type="proposal", actor_type="government", uncertain=True)) | changes


def test_uncertain_lead_is_visible_but_never_a_confirmed_change():
    from src.news_discovery import project_leads
    data = project_leads([row()], country="RS", now=NOW)
    assert data["day"][0]["status"] == "needs_review"
    assert data["day"][0]["russia_relation"] == "uncertain"
    assert data["day"][0]["publisher_country_code"] == "US"
    assert "changes" not in data and "positions" not in data


@pytest.mark.parametrize("changes", [
    {"source_title": "Old source title"}, {"source_excerpt": "Old excerpt"},
    {"published_at": NOW+timedelta(seconds=1)},
    {"published_at": NOW-timedelta(days=8)}, {"collected_at": NOW+timedelta(seconds=1)},
    {"classification": {"countries": ["US"], "russia_relation": "direct"}},
    {"classification": {"countries": ["RS"], "russia_relation": "none"}},
    {"classification": {"countries": "RS", "russia_relation": "direct"}},
])
def test_stale_wrong_country_and_irrelevant_records_cannot_enter_leads(changes):
    from src.news_discovery import project_leads
    assert project_leads([row(**changes)], country="RS", now=NOW) == {"day": [], "week": []}


def test_week_period_missing_translation_and_unsafe_link_are_honest():
    from src.news_discovery import project_leads
    data = project_leads([row(published_at=NOW-timedelta(days=2), title_ru=None,
                             url="javascript:alert(1)")], country="RS", now=NOW)
    assert data["day"] == []
    assert data["week"][0]["title_ru"] is None
    assert data["week"][0]["title_original"].startswith("Serbia")
    assert data["week"][0]["url"] is None


def test_selected_country_is_not_inferred_from_publisher():
    from src.news_discovery import project_leads
    assert project_leads([row(publisher_country_code="RS",
        classification={"countries": [], "russia_relation": "direct"})],
        country="RS", now=NOW)["week"] == []


def test_processing_status_keeps_local_denominator_and_budget_distinct():
    from src.news_discovery import processing_status
    assert processing_status(100, 0, None) == "not_started"
    assert processing_status(100, 10, .02) == "partial"
    assert processing_status(100, 10, .009) == "budget_exhausted"
    assert processing_status(100, 100, .001) == "up_to_date"
    assert processing_status(0, 0, None) == "not_started"


def test_nato_russia_lead_stays_in_explicit_global_bucket_without_inventing_member_countries():
    from src.news_discovery import project_leads
    item = row(classification=dict(countries=[], russia_relation="direct",topic="security",
                                   event_type="statement",actor_type="government"))
    assert project_leads([item], country="RS", now=NOW)["week"] == []
    global_leads = project_leads([item], country=None, now=NOW)
    assert global_leads["day"][0]["countries"] == []
    assert global_leads["day"][0]["status"] == "needs_review"


@pytest.mark.parametrize("language,title,expected", [
    ("ru", "Российские граждане обсуждают новые правила", "Российские граждане обсуждают новые правила"),
    ("sr", "Србија и Русија разговарају", None),
    (None, "Србија и Русија разговарају", None),
    ("en", "Russia and Serbia discuss new rules", None),
])
def test_russian_original_needs_no_paid_translation(language, title, expected):
    from src.news_discovery import project_leads
    item = row(language=language, title=title, source_title=title, title_ru=None)
    result = project_leads([item], country="RS", now=NOW)["day"][0]
    assert result["title_ru"] == expected
    assert result["title_original"] == title
    assert result["status"] == "needs_review"


@pytest.mark.parametrize("primary", ["unknown", "none", None])
def test_secondary_country_never_replaces_unresolved_primary(primary):
    from src.news_discovery import project_leads
    item = row()
    item["classification"].update(country_primary=primary, country_secondary="US", countries=["US"])
    assert project_leads([item], country="US", now=NOW)["week"] == []
    global_items = project_leads([item], country=None, now=NOW)["week"]
    assert global_items[0]["countries"] == []


def test_resolved_bilateral_countries_are_preserved():
    from src.news_discovery import project_leads
    item = row()
    item["classification"].update(country_primary="RS", country_secondary="US", countries=["RS", "US"])
    assert project_leads([item], country="US", now=NOW)["day"][0]["countries"] == ["RS", "US"]


@pytest.mark.parametrize("primary", ["RS", "unknown"])
def test_malformed_country_array_never_becomes_a_country_or_global_lead(primary):
    from src.news_discovery import project_leads
    item = row()
    item["classification"].update(country_primary=primary, countries=["RS", 42])
    for country in ("RS", None):
        assert project_leads([item], country=country, now=NOW)["week"] == []
