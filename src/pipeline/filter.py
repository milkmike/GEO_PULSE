"""Keyword-based relevance filter."""
import re

# Keywords indicating Russia-related content
RUSSIA_KEYWORDS = re.compile(
    r"Росси|Росі|Путин|Москв|Кремл|"
    r"\b(?:ОДКБ|ЕАЭС|ШОС|СНГ|CSTO|EAEU|SCO|CIS)\b|"
    r"\b(?:Russia\w*|Russland\w*|russisch\w*|Rusia\w*|"
    r"Russie|russe\w*|Rússia|Rusya|Rusiya|Rosja|Rosji|Rosją|Rosję|"
    r"rosyj\w*|Rusko|Ruska|Venäjä\w*|Ryssland\w*|Putin\w*|Poutine|Kremlin)\b|"
    r"روسيا|روسیه|الروسي|بوتين|"
    r"俄罗斯|俄羅斯|普京|ロシア|プーチン|러시아|푸틴|रूस|"
    r"Лавров|лавров|Lavrov|"
    r"Мишустин|мишустин|"
    r"Газпром|газпром|Роснефть|роснефть|"
    r"Росатом|росатом|"
    r"рубл[ьяей]|"
    r"санкци[яий]|"
    r"\b(?:НАТО|NATO)\b",
    re.IGNORECASE,
)


def is_relevant(title: str, body: str = "") -> bool:
    """Check if article mentions Russia or related topics."""
    text = f"{title} {body}"
    return bool(RUSSIA_KEYWORDS.search(text))
