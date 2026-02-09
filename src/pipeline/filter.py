"""Keyword-based relevance filter."""
import re

# Keywords indicating Russia-related content
RUSSIA_KEYWORDS = re.compile(
    r"Росси|ОДКБ|ЕАЭС|Путин|Москв|Кремл|ШОС|СНГ|"
    r"росси|одкб|еаэс|путин|москв|кремл|шос|снг|"
    r"Russia|Putin|Kremlin|CSTO|EAEU|SCO|CIS|"
    r"Лавров|лавров|Lavrov|"
    r"Мишустин|мишустин|"
    r"Газпром|газпром|Роснефть|роснефть|"
    r"Росатом|росатом|"
    r"рубл[ьяей]|"
    r"санкци[яий]|"
    r"НАТО|NATO",
    re.IGNORECASE,
)


def is_relevant(title: str, body: str = "") -> bool:
    """Check if article mentions Russia or related topics."""
    text = f"{title} {body}"
    return bool(RUSSIA_KEYWORDS.search(text))
