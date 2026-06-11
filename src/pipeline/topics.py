"""Topic taxonomy — thematic lenses on the country↔Russia relationship.

16 topics (worldmonitor has 15 feed categories; ours are Russia-relation
specific). An article gets 1–3 labels in addition to its primary event_type.
"""

TOPICS = {
    "diplomacy": "Дипломатия",
    "military": "Военное",
    "security": "Безопасность и разведка",
    "economy_trade": "Экономика и торговля",
    "energy": "Энергетика",
    "sanctions": "Санкции",
    "finance": "Финансы и валюта",
    "technology": "Технологии",
    "science_space": "Наука и космос",
    "culture_sport": "Культура и спорт",
    "migration": "Миграция и диаспора",
    "information_influence": "Информационное влияние",
    "history_memory": "Историческая память",
    "organizations": "Международные организации",
    "ukraine_war": "Война в Украине (позиция страны)",
    "espionage": "Шпионаж и спецслужбы",
}

TOPICS_PROMPT_LIST = " | ".join(TOPICS.keys())


def validate_topics(raw) -> list[str]:
    """Keep only known taxonomy labels, max 3, preserve order."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",")]
    seen = []
    for t in raw:
        key = str(t).strip().lower().replace("-", "_").replace(" ", "_")
        if key in TOPICS and key not in seen:
            seen.append(key)
        if len(seen) == 3:
            break
    return seen
