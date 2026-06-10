"""Global country registry for world-wide Russia-relations monitoring.

Each country is monitored through the lens of its relations with Russia:
  - tier 1: deep coverage — own media sources, LLM sentiment pipeline, temperature
  - tier 2: global coverage — GDELT tone/volume + structural baseline

Fields per country:
  name_ru, name_en, iso3, fips (GDELT sourcecountry code, FIPS 10-4),
  flag, region, tier, memberships, flags (unfriendly/sanctions/war), baseline_adj.

`baseline_adj` is a curated structural correction (e.g. declared strategic
partnerships that are not captured by bloc membership). Keep adjustments
documented in `baseline_note`.
"""

REGIONS = {
    "cis": "Постсоветское пространство",
    "west_europe": "Западная Европа",
    "east_europe": "Восточная Европа",
    "balkans": "Балканы и Юго-Восточная Европа",
    "north_america": "Северная Америка",
    "latin_america": "Латинская Америка",
    "middle_east": "Ближний Восток",
    "north_africa": "Северная Африка",
    "africa": "Африка",
    "east_asia": "Восточная Азия",
    "south_asia": "Южная Азия",
    "se_asia": "Юго-Восточная Азия",
    "oceania": "Океания",
}

# code: (name_ru, name_en, iso3, fips, flag, region, tier,
#        "memberships,csv", "flags,csv", baseline_adj, baseline_note)
_REGISTRY = {
    # ── Постсоветское пространство (tier 1 — глубокое покрытие) ──
    "KZ": ("Казахстан", "Kazakhstan", "KAZ", "KZ", "🇰🇿", "cis", 1,
           "cis,eaeu,csto,sco", "", 0, ""),
    "BY": ("Беларусь", "Belarus", "BLR", "BO", "🇧🇾", "cis", 1,
           "cis,eaeu,csto,union_state,sco", "", 0, ""),
    "AM": ("Армения", "Armenia", "ARM", "AM", "🇦🇲", "cis", 1,
           "cis,eaeu,csto_suspended", "", 0, "участие в ОДКБ заморожено"),
    "AZ": ("Азербайджан", "Azerbaijan", "AZE", "AJ", "🇦🇿", "cis", 1,
           "cis", "", 0, ""),
    "GE": ("Грузия", "Georgia", "GEO", "GG", "🇬🇪", "cis", 1,
           "", "", 0, "вышла из СНГ в 2009"),
    "MD": ("Молдова", "Moldova", "MDA", "MD", "🇲🇩", "cis", 1,
           "cis", "", 0, "сворачивает участие в СНГ"),
    "UZ": ("Узбекистан", "Uzbekistan", "UZB", "UZ", "🇺🇿", "cis", 1,
           "cis,sco", "", 0, ""),
    "KG": ("Кыргызстан", "Kyrgyzstan", "KGZ", "KG", "🇰🇬", "cis", 1,
           "cis,eaeu,csto,sco", "", 0, ""),
    "TJ": ("Таджикистан", "Tajikistan", "TJK", "TI", "🇹🇯", "cis", 1,
           "cis,csto,sco", "", 0, ""),
    "TM": ("Туркменистан", "Turkmenistan", "TKM", "TX", "🇹🇲", "cis", 1,
           "cis", "", 0, "ассоциированный член СНГ, нейтралитет"),

    # ── Западная Европа ──
    "GB": ("Великобритания", "United Kingdom", "GBR", "UK", "🇬🇧", "west_europe", 2,
           "nato,g7", "unfriendly,sanctions", 0, ""),
    "DE": ("Германия", "Germany", "DEU", "GM", "🇩🇪", "west_europe", 2,
           "nato,eu,g7", "unfriendly,sanctions", 0, ""),
    "FR": ("Франция", "France", "FRA", "FR", "🇫🇷", "west_europe", 2,
           "nato,eu,g7", "unfriendly,sanctions", 0, ""),
    "IT": ("Италия", "Italy", "ITA", "IT", "🇮🇹", "west_europe", 2,
           "nato,eu,g7", "unfriendly,sanctions", 0, ""),
    "ES": ("Испания", "Spain", "ESP", "SP", "🇪🇸", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "PT": ("Португалия", "Portugal", "PRT", "PO", "🇵🇹", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "NL": ("Нидерланды", "Netherlands", "NLD", "NL", "🇳🇱", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "BE": ("Бельгия", "Belgium", "BEL", "BE", "🇧🇪", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "AT": ("Австрия", "Austria", "AUT", "AU", "🇦🇹", "west_europe", 2,
           "eu", "unfriendly,sanctions", 0, "военный нейтралитет"),
    "CH": ("Швейцария", "Switzerland", "CHE", "SZ", "🇨🇭", "west_europe", 2,
           "", "unfriendly,sanctions", 0, "нейтралитет, но присоединилась к санкциям"),
    "SE": ("Швеция", "Sweden", "SWE", "SW", "🇸🇪", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "NO": ("Норвегия", "Norway", "NOR", "NO", "🇳🇴", "west_europe", 2,
           "nato", "unfriendly,sanctions", 0, ""),
    "FI": ("Финляндия", "Finland", "FIN", "FI", "🇫🇮", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "DK": ("Дания", "Denmark", "DNK", "DA", "🇩🇰", "west_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "IE": ("Ирландия", "Ireland", "IRL", "EI", "🇮🇪", "west_europe", 2,
           "eu", "unfriendly,sanctions", 0, ""),
    "IS": ("Исландия", "Iceland", "ISL", "IC", "🇮🇸", "west_europe", 2,
           "nato", "unfriendly,sanctions", 0, ""),

    # ── Восточная Европа ──
    "UA": ("Украина", "Ukraine", "UKR", "UP", "🇺🇦", "east_europe", 2,
           "", "unfriendly,sanctions,war", 0, "вооружённый конфликт"),
    "PL": ("Польша", "Poland", "POL", "PL", "🇵🇱", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "CZ": ("Чехия", "Czechia", "CZE", "EZ", "🇨🇿", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "SK": ("Словакия", "Slovakia", "SVK", "LO", "🇸🇰", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 5, "прагматичный курс правительства"),
    "HU": ("Венгрия", "Hungary", "HUN", "HU", "🇭🇺", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 10, "особая позиция Будапешта"),
    "RO": ("Румыния", "Romania", "ROU", "RO", "🇷🇴", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "BG": ("Болгария", "Bulgaria", "BGR", "BU", "🇧🇬", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "EE": ("Эстония", "Estonia", "EST", "EN", "🇪🇪", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "LV": ("Латвия", "Latvia", "LVA", "LG", "🇱🇻", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "LT": ("Литва", "Lithuania", "LTU", "LH", "🇱🇹", "east_europe", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),

    # ── Балканы ──
    "RS": ("Сербия", "Serbia", "SRB", "RI", "🇷🇸", "balkans", 2,
           "", "", 15, "исторический партнёр, не вводила санкции"),
    "GR": ("Греция", "Greece", "GRC", "GR", "🇬🇷", "balkans", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "CY": ("Кипр", "Cyprus", "CYP", "CY", "🇨🇾", "balkans", 2,
           "eu", "unfriendly,sanctions", 0, ""),
    "HR": ("Хорватия", "Croatia", "HRV", "HR", "🇭🇷", "balkans", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "SI": ("Словения", "Slovenia", "SVN", "SI", "🇸🇮", "balkans", 2,
           "nato,eu", "unfriendly,sanctions", 0, ""),
    "BA": ("Босния и Герцеговина", "Bosnia and Herzegovina", "BIH", "BK", "🇧🇦", "balkans", 2,
           "", "", 5, "не присоединилась к санкциям (фактор Республики Сербской)"),
    "MK": ("Северная Македония", "North Macedonia", "MKD", "MK", "🇲🇰", "balkans", 2,
           "nato", "unfriendly,sanctions", 0, ""),
    "ME": ("Черногория", "Montenegro", "MNE", "MJ", "🇲🇪", "balkans", 2,
           "nato", "unfriendly,sanctions", 0, ""),
    "AL": ("Албания", "Albania", "ALB", "AL", "🇦🇱", "balkans", 2,
           "nato", "unfriendly,sanctions", 0, ""),

    # ── Северная Америка ──
    "US": ("США", "United States", "USA", "US", "🇺🇸", "north_america", 2,
           "nato,g7", "unfriendly,sanctions", 0, ""),
    "CA": ("Канада", "Canada", "CAN", "CA", "🇨🇦", "north_america", 2,
           "nato,g7", "unfriendly,sanctions", 0, ""),
    "MX": ("Мексика", "Mexico", "MEX", "MX", "🇲🇽", "north_america", 2,
           "", "", 0, ""),

    # ── Латинская Америка ──
    "BR": ("Бразилия", "Brazil", "BRA", "BR", "🇧🇷", "latin_america", 2,
           "brics", "", 0, ""),
    "AR": ("Аргентина", "Argentina", "ARG", "AR", "🇦🇷", "latin_america", 2,
           "", "", 0, ""),
    "VE": ("Венесуэла", "Venezuela", "VEN", "VE", "🇻🇪", "latin_america", 2,
           "", "", 25, "стратегический партнёр"),
    "CU": ("Куба", "Cuba", "CUB", "CU", "🇨🇺", "latin_america", 2,
           "", "", 25, "стратегический партнёр"),
    "NI": ("Никарагуа", "Nicaragua", "NIC", "NU", "🇳🇮", "latin_america", 2,
           "", "", 20, "союзнические отношения"),
    "CL": ("Чили", "Chile", "CHL", "CI", "🇨🇱", "latin_america", 2,
           "", "", 0, ""),
    "CO": ("Колумбия", "Colombia", "COL", "CO", "🇨🇴", "latin_america", 2,
           "", "", 0, ""),
    "PE": ("Перу", "Peru", "PER", "PE", "🇵🇪", "latin_america", 2,
           "", "", 0, ""),
    "BO": ("Боливия", "Bolivia", "BOL", "BL", "🇧🇴", "latin_america", 2,
           "", "", 15, "партнёр БРИКС, военно-техническое сотрудничество"),

    # ── Ближний Восток ──
    "TR": ("Турция", "Turkey", "TUR", "TU", "🇹🇷", "middle_east", 2,
           "nato", "", 10, "многовекторность, не вводила санкции"),
    "IR": ("Иран", "Iran", "IRN", "IR", "🇮🇷", "middle_east", 2,
           "sco,brics", "", 15, "договор о стратегическом партнёрстве 2025"),
    "IL": ("Израиль", "Israel", "ISR", "IS", "🇮🇱", "middle_east", 2,
           "", "", 0, ""),
    "SA": ("Саудовская Аравия", "Saudi Arabia", "SAU", "SA", "🇸🇦", "middle_east", 2,
           "", "", 5, "координация в ОПЕК+"),
    "AE": ("ОАЭ", "United Arab Emirates", "ARE", "AE", "🇦🇪", "middle_east", 2,
           "brics", "", 5, "торгово-финансовый хаб"),
    "QA": ("Катар", "Qatar", "QAT", "QA", "🇶🇦", "middle_east", 2,
           "", "", 0, ""),
    "IQ": ("Ирак", "Iraq", "IRQ", "IZ", "🇮🇶", "middle_east", 2,
           "", "", 0, ""),
    "SY": ("Сирия", "Syria", "SYR", "SY", "🇸🇾", "middle_east", 2,
           "", "", 0, "статус после смены власти неопределён"),
    "EG": ("Египет", "Egypt", "EGY", "EG", "🇪🇬", "middle_east", 2,
           "brics", "", 5, ""),

    # ── Восточная Азия ──
    "CN": ("Китай", "China", "CHN", "CH", "🇨🇳", "east_asia", 2,
           "sco,brics", "", 15, "всеобъемлющее стратегическое партнёрство"),
    "JP": ("Япония", "Japan", "JPN", "JA", "🇯🇵", "east_asia", 2,
           "g7", "unfriendly,sanctions", 0, ""),
    "KR": ("Южная Корея", "South Korea", "KOR", "KS", "🇰🇷", "east_asia", 2,
           "", "unfriendly,sanctions", 0, ""),
    "KP": ("КНДР", "North Korea", "PRK", "KN", "🇰🇵", "east_asia", 2,
           "", "", 40, "договор о всеобъемлющем стратегическом партнёрстве 2024"),
    "MN": ("Монголия", "Mongolia", "MNG", "MG", "🇲🇳", "east_asia", 2,
           "", "", 5, ""),
    "TW": ("Тайвань", "Taiwan", "TWN", "TW", "🇹🇼", "east_asia", 2,
           "", "unfriendly,sanctions", 0, ""),

    # ── Южная Азия ──
    "IN": ("Индия", "India", "IND", "IN", "🇮🇳", "south_asia", 2,
           "sco,brics", "", 10, "особо привилегированное стратегическое партнёрство"),
    "PK": ("Пакистан", "Pakistan", "PAK", "PK", "🇵🇰", "south_asia", 2,
           "sco", "", 0, ""),
    "AF": ("Афганистан", "Afghanistan", "AFG", "AF", "🇦🇫", "south_asia", 2,
           "", "", 0, ""),
    "BD": ("Бангладеш", "Bangladesh", "BGD", "BG", "🇧🇩", "south_asia", 2,
           "", "", 0, ""),
    "LK": ("Шри-Ланка", "Sri Lanka", "LKA", "CE", "🇱🇰", "south_asia", 2,
           "", "", 0, ""),

    # ── Юго-Восточная Азия ──
    "ID": ("Индонезия", "Indonesia", "IDN", "ID", "🇮🇩", "se_asia", 2,
           "brics", "", 0, ""),
    "VN": ("Вьетнам", "Vietnam", "VNM", "VM", "🇻🇳", "se_asia", 2,
           "", "", 10, "всеобъемлющее стратегическое партнёрство"),
    "TH": ("Таиланд", "Thailand", "THA", "TH", "🇹🇭", "se_asia", 2,
           "", "", 0, ""),
    "MY": ("Малайзия", "Malaysia", "MYS", "MY", "🇲🇾", "se_asia", 2,
           "", "", 0, ""),
    "SG": ("Сингапур", "Singapore", "SGP", "SN", "🇸🇬", "se_asia", 2,
           "", "unfriendly,sanctions", 0, ""),
    "PH": ("Филиппины", "Philippines", "PHL", "RP", "🇵🇭", "se_asia", 2,
           "", "", 0, ""),
    "MM": ("Мьянма", "Myanmar", "MMR", "BM", "🇲🇲", "se_asia", 2,
           "", "", 15, "военно-техническое сотрудничество"),

    # ── Африка ──
    "ZA": ("ЮАР", "South Africa", "ZAF", "SF", "🇿🇦", "africa", 2,
           "brics", "", 5, ""),
    "NG": ("Нигерия", "Nigeria", "NGA", "NI", "🇳🇬", "africa", 2,
           "", "", 0, ""),
    "ET": ("Эфиопия", "Ethiopia", "ETH", "ET", "🇪🇹", "africa", 2,
           "brics", "", 5, ""),
    "KE": ("Кения", "Kenya", "KEN", "KE", "🇰🇪", "africa", 2,
           "", "", 0, ""),
    "DZ": ("Алжир", "Algeria", "DZA", "AG", "🇩🇿", "north_africa", 2,
           "", "", 10, "крупный покупатель вооружений"),
    "MA": ("Марокко", "Morocco", "MAR", "MO", "🇲🇦", "north_africa", 2,
           "", "", 0, ""),
    "TN": ("Тунис", "Tunisia", "TUN", "TS", "🇹🇳", "north_africa", 2,
           "", "", 0, ""),
    "LY": ("Ливия", "Libya", "LBY", "LY", "🇱🇾", "north_africa", 2,
           "", "", 5, ""),
    "SD": ("Судан", "Sudan", "SDN", "SU", "🇸🇩", "africa", 2,
           "", "", 10, "соглашение о военно-морской базе"),
    "ML": ("Мали", "Mali", "MLI", "ML", "🇲🇱", "africa", 2,
           "", "", 25, "военное сотрудничество, Африканский корпус"),
    "NE": ("Нигер", "Niger", "NER", "NG", "🇳🇪", "africa", 2,
           "", "", 20, "военное сотрудничество"),
    "BF": ("Буркина-Фасо", "Burkina Faso", "BFA", "UV", "🇧🇫", "africa", 2,
           "", "", 20, "военное сотрудничество"),
    "CF": ("ЦАР", "Central African Republic", "CAF", "CT", "🇨🇫", "africa", 2,
           "", "", 25, "военное присутствие"),

    # ── Океания ──
    "AU": ("Австралия", "Australia", "AUS", "AS", "🇦🇺", "oceania", 2,
           "", "unfriendly,sanctions", 0, ""),
    "NZ": ("Новая Зеландия", "New Zealand", "NZL", "NZ", "🇳🇿", "oceania", 2,
           "", "unfriendly,sanctions", 0, ""),
}


def _expand(code: str, row: tuple) -> dict:
    name_ru, name_en, iso3, fips, flag, region, tier, mships, flags, adj, note = row
    flag_set = {f for f in flags.split(",") if f}
    return {
        "code": code,
        "name_ru": name_ru,
        "name_en": name_en,
        "iso3": iso3,
        "fips": fips,
        "flag": flag,
        "region": region,
        "tier": tier,
        "memberships": [m for m in mships.split(",") if m],
        "unfriendly": "unfriendly" in flag_set,
        "sanctions_on_russia": "sanctions" in flag_set,
        "war_with_russia": "war" in flag_set,
        "baseline_adj": adj,
        "baseline_note": note,
    }


COUNTRIES: dict[str, dict] = {code: _expand(code, row) for code, row in _REGISTRY.items()}

COUNTRY_NAMES_ALL = {code: c["name_ru"] for code, c in COUNTRIES.items()}


def get_country(code: str) -> dict | None:
    return COUNTRIES.get((code or "").upper())


def country_name_ru(code: str) -> str:
    c = get_country(code)
    return c["name_ru"] if c else code


def all_codes() -> list[str]:
    return list(COUNTRIES.keys())


def tier1_codes() -> list[str]:
    return [code for code, c in COUNTRIES.items() if c["tier"] == 1]


def gdelt_countries() -> list[dict]:
    """Countries with a FIPS code — monitorable via GDELT."""
    return [c for c in COUNTRIES.values() if c["fips"]]


def ensure_countries_in_db():
    """Sync the registry into the countries table (insert or update)."""
    from sqlalchemy import text
    from src.db import get_session

    with get_session() as session:
        for c in COUNTRIES.values():
            session.execute(
                text("""
                    INSERT INTO countries (code, name_ru, name_en, iso3, fips, flag,
                                           region, tier, memberships, unfriendly,
                                           sanctions_on_russia, war_with_russia,
                                           baseline_adj, baseline_note, active, updated_at)
                    VALUES (:code, :name_ru, :name_en, :iso3, :fips, :flag,
                            :region, :tier, :memberships, :unfriendly,
                            :sanctions, :war, :adj, :note, TRUE, NOW())
                    ON CONFLICT (code) DO UPDATE SET
                        name_ru = EXCLUDED.name_ru,
                        name_en = EXCLUDED.name_en,
                        iso3 = EXCLUDED.iso3,
                        fips = EXCLUDED.fips,
                        flag = EXCLUDED.flag,
                        region = EXCLUDED.region,
                        tier = EXCLUDED.tier,
                        memberships = EXCLUDED.memberships,
                        unfriendly = EXCLUDED.unfriendly,
                        sanctions_on_russia = EXCLUDED.sanctions_on_russia,
                        war_with_russia = EXCLUDED.war_with_russia,
                        baseline_adj = EXCLUDED.baseline_adj,
                        baseline_note = EXCLUDED.baseline_note,
                        updated_at = NOW()
                """),
                {
                    "code": c["code"], "name_ru": c["name_ru"], "name_en": c["name_en"],
                    "iso3": c["iso3"], "fips": c["fips"], "flag": c["flag"],
                    "region": c["region"], "tier": c["tier"],
                    "memberships": c["memberships"], "unfriendly": c["unfriendly"],
                    "sanctions": c["sanctions_on_russia"], "war": c["war_with_russia"],
                    "adj": c["baseline_adj"], "note": c["baseline_note"],
                },
            )
