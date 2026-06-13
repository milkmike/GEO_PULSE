"""Google News RSS helpers — the worldmonitor "universal adapter".

Google News search RSS is not geoblocked, always returns a valid feed, and can
be scoped to a country edition + language. We use it two ways:

  site_wrapper_url(url, lang)   — route a specific outlet's coverage through
                                  Google News (used to revive geoblocked/dead
                                  direct feeds; see scripts/fix_dead_feeds.py).
  native_feed_url(cc, lang)     — a country's own-language news ABOUT Russia
                                  (used to fill native-language coverage gaps;
                                  see scripts/add_native_feeds.py).

RUSSIA_TERM/LOCALE cover the registry's languages (src/countries.py). Anything
missing falls back to English — and every generated feed is validated live
before use, so an imperfect locale just gets skipped, never shipped broken.
"""
from urllib.parse import quote, urlparse

GNEWS = "https://news.google.com/rss/search"

# "Russia" in each language we collect in.
RUSSIA_TERM = {
    "en": "russia", "ru": "Россия", "uk": "Росія", "be": "Расія",
    "kk": "Ресей", "ky": "Орусия", "uz": "Rossiya", "tg": "Русия",
    "tk": "Russiýa", "hy": "Ռուսաստան", "ka": "რუსეთი", "az": "Rusiya",
    "de": "Russland", "fr": "Russie", "it": "Russia", "es": "Rusia",
    "pt": "Rússia", "nl": "Rusland", "sv": "Ryssland", "no": "Russland",
    "fi": "Venäjä", "da": "Rusland", "is": "Rússland",
    "pl": "Rosja", "cs": "Rusko", "sk": "Rusko", "hu": "Oroszország",
    "ro": "Rusia", "bg": "Русия", "et": "Venemaa", "lv": "Krievija",
    "lt": "Rusija", "sr": "Русија", "el": "Ρωσία", "hr": "Rusija",
    "sl": "Rusija", "bs": "Rusija", "mk": "Русија", "sq": "Rusia",
    "tr": "Rusya", "fa": "روسیه", "he": "רוסיה", "ar": "روسيا",
    "zh": "俄罗斯", "ja": "ロシア", "ko": "러시아", "mn": "Орос",
    "hi": "रूस", "ur": "روس", "ps": "روسیه", "bn": "রাশিয়া",
    "si": "රුසියාව", "id": "Rusia", "vi": "Nga", "th": "รัสเซีย",
    "ms": "Rusia", "my": "ရုရှား", "am": "ሩሲያ", "sw": "Urusi",
}

# lang -> (hl, gl_default, ceid). gl_default/ceid are the language's "home"
# edition, used for site wrappers; native feeds override gl with the country.
LOCALE = {
    "en": ("en-US", "US", "US:en"), "ru": ("ru", "RU", "RU:ru"),
    "uk": ("uk", "UA", "UA:uk"), "be": ("be", "BY", "BY:be"),
    "kk": ("kk", "KZ", "KZ:kk"), "ky": ("ky", "KG", "KG:ky"),
    "uz": ("uz", "UZ", "UZ:uz"), "tg": ("tg", "TJ", "TJ:tg"),
    "tk": ("tk", "TM", "TM:tk"), "hy": ("hy", "AM", "AM:hy"),
    "ka": ("ka", "GE", "GE:ka"), "az": ("az", "AZ", "AZ:az"),
    "de": ("de", "DE", "DE:de"), "fr": ("fr", "FR", "FR:fr"),
    "it": ("it", "IT", "IT:it"), "es": ("es-419", "MX", "MX:es-419"),
    "pt": ("pt-BR", "BR", "BR:pt-419"), "nl": ("nl", "NL", "NL:nl"),
    "sv": ("sv", "SE", "SE:sv"), "no": ("no", "NO", "NO:no"),
    "fi": ("fi", "FI", "FI:fi"), "da": ("da", "DK", "DK:da"),
    "is": ("is", "IS", "IS:is"), "pl": ("pl", "PL", "PL:pl"),
    "cs": ("cs", "CZ", "CZ:cs"), "sk": ("sk", "SK", "SK:sk"),
    "hu": ("hu", "HU", "HU:hu"), "ro": ("ro", "RO", "RO:ro"),
    "bg": ("bg", "BG", "BG:bg"), "et": ("et", "EE", "EE:et"),
    "lv": ("lv", "LV", "LV:lv"), "lt": ("lt", "LT", "LT:lt"),
    "sr": ("sr", "RS", "RS:sr"), "el": ("el", "GR", "GR:el"),
    "hr": ("hr", "HR", "HR:hr"), "sl": ("sl", "SI", "SI:sl"),
    "bs": ("bs", "BA", "BA:bs"), "mk": ("mk", "MK", "MK:mk"),
    "sq": ("sq", "AL", "AL:sq"), "tr": ("tr", "TR", "TR:tr"),
    "fa": ("fa", "IR", "IR:fa"), "he": ("he", "IL", "IL:he"),
    "ar": ("ar", "EG", "EG:ar"), "zh": ("zh-CN", "CN", "CN:zh-Hans"),
    "ja": ("ja", "JP", "JP:ja"), "ko": ("ko", "KR", "KR:ko"),
    "mn": ("mn", "MN", "MN:mn"), "hi": ("hi", "IN", "IN:hi"),
    "ur": ("ur", "PK", "PK:ur"), "ps": ("ps", "AF", "AF:ps"),
    "bn": ("bn", "BD", "BD:bn"), "si": ("si", "LK", "LK:si"),
    "id": ("id", "ID", "ID:id"), "vi": ("vi", "VN", "VN:vi"),
    "th": ("th", "TH", "TH:th"), "ms": ("ms", "MY", "MY:ms"),
    "my": ("my", "MM", "MM:my"), "am": ("am", "ET", "ET:am"),
    "sw": ("sw", "KE", "KE:sw"),
}


def base_domain(url: str) -> str:
    """Registrable-ish host for a site: filter (strip common feed prefixes)."""
    host = urlparse(url).netloc.lower()
    for p in ("rss.", "www.", "feeds.", "feed.", "en.", "amp.", "m."):
        if host.startswith(p):
            host = host[len(p):]
    return host


def site_wrapper_url(url: str, lang: str) -> str:
    """Route a specific outlet's Russia coverage through Google News."""
    domain = base_domain(url)
    term = RUSSIA_TERM.get(lang, "russia")
    hl, gl, ceid = LOCALE.get(lang, ("en-US", "US", "US:en"))
    return f"{GNEWS}?q=site:{domain}+{term}&hl={hl}&gl={gl}&ceid={ceid}"


def native_feed_url(country_code: str, lang: str) -> str:
    """A country's own-language Google News edition, queried about Russia."""
    term = RUSSIA_TERM.get(lang, "Russia")
    hl, _gl, ceid = LOCALE.get(lang, (lang, country_code, f"{country_code}:{lang}"))
    suffix = ceid.split(":", 1)[1] if ":" in ceid else lang
    cc = country_code.upper()
    return f"{GNEWS}?q={quote(term)}&hl={hl}&gl={cc}&ceid={cc}:{suffix}"
