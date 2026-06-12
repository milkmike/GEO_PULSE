from src.pipeline.agreements import group_agreements


def make_row(event_key, title, url, source, published_at, action_level, event_type):
    return {"event_key": event_key, "title": title, "url": url, "source": source,
            "published_at": published_at, "action_level": action_level,
            "event_type": event_type}


def test_groups_by_event_key_sorted_by_recency():
    rows = [
        make_row("gas deal", "A", "u1", "s1", "2026-06-01", 3, "economic"),
        make_row("gas deal", "B", "u2", "s2", "2026-06-02", 4, "economic"),
        make_row("summit", "C", "u3", "s3", "2026-06-05", 3, "diplomatic"),
    ]
    groups = group_agreements(rows, max_articles=5)
    assert [g["event_key"] for g in groups] == ["summit", "gas deal"]
    gas = groups[1]
    assert gas["action_level"] == 4
    assert gas["articles_total"] == 2
    assert gas["last_at"] == "2026-06-02"


def test_caps_articles_per_group():
    rows = [make_row("k", f"t{i}", f"u{i}", "s", f"2026-06-{i+1:02d}", 3, "diplomatic")
            for i in range(8)]
    g = group_agreements(rows, max_articles=3)[0]
    assert len(g["articles"]) == 3
    assert g["articles_total"] == 8
    assert g["articles"][0]["title"] == "t7"  # newest first (dates 06-01..06-08 for t0..t7)


def test_skips_empty_event_keys():
    rows = [make_row("", "A", "u", "s", "2026-06-01", 3, "diplomatic"),
            make_row(None, "B", "u", "s", "2026-06-01", 3, "diplomatic")]
    assert group_agreements(rows) == []
