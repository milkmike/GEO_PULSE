"""Group diplomatic/economic high-action events into agreement cards."""
from typing import Dict, List


def group_agreements(rows: List[dict], max_articles: int = 5) -> List[dict]:
    """rows: flat article rows with event_key; returns grouped cards, newest first."""
    groups: Dict[str, dict] = {}
    for r in rows:
        key = (r.get("event_key") or "").strip()
        if not key:
            continue
        g = groups.setdefault(key, {
            "event_key": key, "event_type": r["event_type"],
            "action_level": 0, "first_at": r["published_at"],
            "last_at": r["published_at"], "articles": [], "articles_total": 0,
        })
        g["action_level"] = max(g["action_level"], int(r.get("action_level") or 0))
        g["first_at"] = min(g["first_at"], r["published_at"])
        g["last_at"] = max(g["last_at"], r["published_at"])
        g["articles_total"] += 1
        g["articles"].append({"title": r["title"], "url": r["url"],
                              "source": r["source"],
                              "published_at": r["published_at"]})
    for g in groups.values():
        g["articles"].sort(key=lambda a: a["published_at"], reverse=True)
        g["articles"] = g["articles"][:max_articles]
    return sorted(groups.values(), key=lambda g: g["last_at"], reverse=True)
