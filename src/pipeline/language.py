"""Trust script-compatible language declarations; abstain for ambiguous content.

Script alone does not distinguish English/German or Russian/Ukrainian. This is
not a statistical language detector; ambiguous content is explicitly `und`.
"""
import re
import unicodedata

SCRIPT_LANGUAGES = {
    "LATIN": {"en", "de", "fr", "es", "pt", "it", "nl", "pl", "cs", "sk", "sl", "hr",
              "ro", "tr", "az", "uz", "tk", "id", "ms", "vi", "fi", "sv", "no", "da",
              "et", "lv", "lt", "hu", "sq", "sr", "bs", "sw"},
    "CYRILLIC": {"ru", "uk", "be", "bg", "sr", "mk", "kk", "ky", "tg", "uz", "mn"},
    "ARABIC": {"ar", "fa", "ur", "ps", "ku"},
    "CJK": {"zh", "ja"},
    "HIRAGANA": {"ja"}, "KATAKANA": {"ja"}, "HANGUL": {"ko"},
    "HEBREW": {"he", "yi"}, "GREEK": {"el"}, "ARMENIAN": {"hy"}, "GEORGIAN": {"ka"},
    "DEVANAGARI": {"hi", "mr", "ne"},
}


def detect_language(title: str, declared: str | None = None) -> str:
    letters = [unicodedata.name(c, "") for c in (title or "") if c.isalpha()]
    if len(letters) < 3:
        return "und"
    counts = {script: sum(name.startswith(script) for name in letters) for script in SCRIPT_LANGUAGES}
    if counts["HIRAGANA"] + counts["KATAKANA"] >= 2:
        return "ja"
    script = max(counts, key=counts.get)
    if counts[script] / len(letters) < 0.6:
        return "und"
    hint = re.split(r"[-_]", (declared or "").lower())[0]
    if hint in SCRIPT_LANGUAGES[script]:
        return hint
    languages = SCRIPT_LANGUAGES[script]
    return next(iter(languages)) if len(languages) == 1 else "und"
