"""FX collector — daily CBR (Bank of Russia) exchange rates.

Free XML endpoint, no key: https://www.cbr.ru/scripts/XML_daily.asp
Rates are «RUB per 1 unit of currency». A weakening partner-currency story
or a RUB shock both show up here; the signal layer cross-references moves
with media signals («медиа ведут рынки»).
"""
import logging
import xml.etree.ElementTree as ET
from datetime import date as date_t

import httpx

logger = logging.getLogger(__name__)

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
TIMEOUT = 30.0

# CBR daily list → registry countries using that currency
CURRENCY_COUNTRIES: dict[str, list[str]] = {
    "USD": ["US"], "GBP": ["GB"], "CHF": ["CH"], "CAD": ["CA"],
    "AUD": ["AU"], "NZD": ["NZ"], "JPY": ["JP"], "KRW": ["KR"],
    "CNY": ["CN"], "INR": ["IN"], "BRL": ["BR"], "ZAR": ["ZA"],
    "TRY": ["TR"], "AED": ["AE"], "QAR": ["QA"], "EGP": ["EG"],
    "IDR": ["ID"], "VND": ["VN"], "THB": ["TH"], "SGD": ["SG"],
    "RSD": ["RS"], "BGN": ["BG"], "HUF": ["HU"], "PLN": ["PL"],
    "RON": ["RO"], "CZK": ["CZ"], "SEK": ["SE"], "NOK": ["NO"],
    "DKK": ["DK"],
    "EUR": ["DE", "FR", "IT", "ES", "PT", "NL", "BE", "AT", "IE", "FI",
            "GR", "CY", "SK", "SI", "LV", "LT", "EE", "HR", "ME"],
    # post-Soviet
    "KZT": ["KZ"], "AMD": ["AM"], "AZN": ["AZ"], "BYN": ["BY"],
    "GEL": ["GE"], "MDL": ["MD"], "KGS": ["KG"], "TJS": ["TJ"],
    "TMT": ["TM"], "UZS": ["UZ"], "UAH": ["UA"],
}

COUNTRY_CURRENCY: dict[str, str] = {
    cc: cur for cur, codes in CURRENCY_COUNTRIES.items() for cc in codes
}


def fetch_cbr_rates(on_date: date_t | None = None) -> dict[str, float]:
    """Fetch CBR rates (RUB per 1 unit). Optional historical date."""
    params = {}
    if on_date:
        params["date_req"] = on_date.strftime("%d/%m/%Y")
    try:
        resp = httpx.get(CBR_URL, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "GeoPulse/2.0 (research)"})
        resp.raise_for_status()
        # CBR serves windows-1251; ET honours the XML encoding declaration
        root = ET.fromstring(resp.content)
    except (httpx.HTTPError, ET.ParseError) as e:
        logger.warning(f"CBR fetch failed: {e}")
        return {}

    rates: dict[str, float] = {}
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip()
        if code not in CURRENCY_COUNTRIES:
            continue
        try:
            nominal = float((valute.findtext("Nominal") or "1").replace(",", "."))
            value = float((valute.findtext("Value") or "0").replace(",", "."))
        except ValueError:
            continue
        if nominal > 0 and value > 0:
            rates[code] = round(value / nominal, 6)
    return rates
