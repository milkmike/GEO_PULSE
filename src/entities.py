"""Entity registry — Russia-orbit actors for mention tracking & correlation.

worldmonitor pattern: a curated knowledge base of entities with aliases;
match confidence depends on how the entity was found (direct name 0.95,
alias 0.90, keyword 0.70). Aliases cover the languages of monitored media
(ru/en + regional spellings) and are matched case-insensitively.

Categories: person | org_state | org_bloc | company | military | media | concept
"""

# key: (name_ru, name_en, category, [aliases...])
_E = {
    # ── Лица ──
    "putin": ("Владимир Путин", "Vladimir Putin", "person",
              ["путин", "putin", "путін"]),
    "lavrov": ("Сергей Лавров", "Sergey Lavrov", "person",
               ["лавров", "lavrov"]),
    "peskov": ("Дмитрий Песков", "Dmitry Peskov", "person",
               ["песков", "peskov"]),
    "mishustin": ("Михаил Мишустин", "Mikhail Mishustin", "person",
                  ["мишустин", "mishustin"]),
    "medvedev": ("Дмитрий Медведев", "Dmitry Medvedev", "person",
                 ["медведев", "medvedev"]),
    "shoigu": ("Сергей Шойгу", "Sergei Shoigu", "person",
               ["шойгу", "shoigu"]),
    "belousov": ("Андрей Белоусов", "Andrei Belousov", "person",
                 ["белоусов", "belousov"]),
    "naryshkin": ("Сергей Нарышкин", "Sergey Naryshkin", "person",
                  ["нарышкин", "naryshkin"]),

    # ── Государственные органы РФ ──
    "kremlin": ("Кремль", "Kremlin", "org_state",
                ["кремль", "kremlin", "кремл"]),
    "mid_rf": ("МИД России", "Russian Foreign Ministry", "org_state",
               ["мид россии", "мид рф", "russian foreign ministry", "захарова", "zakharova"]),
    "minoborony": ("Минобороны России", "Russian Defence Ministry", "military",
                   ["минобороны россии", "минобороны рф", "russian defence ministry",
                    "russian defense ministry"]),
    "fsb": ("ФСБ", "FSB", "org_state", ["фсб", "fsb"]),
    "gru": ("ГРУ", "GRU", "military", ["гру ", "gru "]),
    "duma": ("Госдума", "State Duma", "org_state",
             ["госдума", "state duma", "государственная дума"]),
    "cbr": ("ЦБ РФ", "Bank of Russia", "org_state",
            ["центробанк россии", "цб рф", "bank of russia", "набиуллина", "nabiullina"]),

    # ── Блоки и организации ──
    "csto": ("ОДКБ", "CSTO", "org_bloc",
             ["одкб", "csto", "collective security treaty"]),
    "eaeu": ("ЕАЭС", "EAEU", "org_bloc",
             ["еаэс", "евразийский экономический союз", "eaeu", "eurasian economic union"]),
    "cis": ("СНГ", "CIS", "org_bloc",
            ["снг", "содружество независимых государств", "commonwealth of independent states"]),
    "sco": ("ШОС", "SCO", "org_bloc",
            ["шос", "шанхайская организация", "shanghai cooperation"]),
    "brics": ("БРИКС", "BRICS", "org_bloc", ["брикс", "brics"]),
    "union_state": ("Союзное государство", "Union State", "org_bloc",
                    ["союзное государство", "union state"]),

    # ── Компании ──
    "gazprom": ("Газпром", "Gazprom", "company", ["газпром", "gazprom"]),
    "rosatom": ("Росатом", "Rosatom", "company", ["росатом", "rosatom"]),
    "rosneft": ("Роснефть", "Rosneft", "company", ["роснефть", "rosneft"]),
    "lukoil": ("Лукойл", "Lukoil", "company", ["лукойл", "lukoil"]),
    "rzd": ("РЖД", "Russian Railways", "company",
            ["ржд", "russian railways"]),
    "rosoboronexport": ("Рособоронэкспорт", "Rosoboronexport", "company",
                        ["рособоронэкспорт", "rosoboronexport"]),
    "wagner": ("Африканский корпус / ЧВК", "Africa Corps / Wagner", "military",
               ["вагнер", "wagner", "африканский корпус", "africa corps", "чвк"]),

    # ── Медиа-инструменты ──
    "rt": ("RT", "RT", "media", ["russia today", " rt "]),
    "sputnik": ("Sputnik", "Sputnik", "media", ["спутник ", "sputnik"]),
    "tass": ("ТАСС", "TASS", "media", ["тасс", "tass"]),

    # ── Концепты и объекты ──
    "sanctions_rf": ("Санкции против РФ", "Sanctions on Russia", "concept",
                     ["санкци", "sanction", "санкція"]),
    "nord_stream": ("Северный поток", "Nord Stream", "concept",
                    ["северный поток", "nord stream", "северные потоки"]),
    "power_of_siberia": ("Сила Сибири", "Power of Siberia", "concept",
                         ["сила сибири", "power of siberia"]),
    "crimea": ("Крым", "Crimea", "concept", ["крым", "crimea", "крим"]),
    "zaes": ("Запорожская АЭС", "Zaporizhzhia NPP", "concept",
             ["запорожская аэс", "zaporizhzhia", "заэс"]),
    "frozen_assets": ("Замороженные активы РФ", "Frozen Russian assets", "concept",
                      ["замороженные активы", "frozen assets", "конфискация активов"]),
    "oil_price_cap": ("Потолок цен на нефть", "Oil price cap", "concept",
                      ["потолок цен", "price cap"]),
    "shadow_fleet": ("Теневой флот", "Shadow fleet", "concept",
                     ["теневой флот", "shadow fleet"]),
    "ruble": ("Рубль", "Ruble", "concept", ["рубль", "ruble", "rouble"]),
    "russian_base": ("Российские военные базы", "Russian military bases", "military",
                     ["российская база", "российской базы", "russian base", "russian military base"]),
    "migrants_rf": ("Мигранты и Россия", "Migrants & Russia", "concept",
                    ["мигрант", "migrant"]),
    "un_vote_rf": ("Голосования ООН по РФ", "UN votes on Russia", "concept",
                   ["голосование в оон", "резолюция оон", "un resolution", "un vote"]),
}

ENTITIES: dict[str, dict] = {
    key: {
        "key": key,
        "name_ru": name_ru,
        "name_en": name_en,
        "category": category,
        "aliases": aliases,
    }
    for key, (name_ru, name_en, category, aliases) in _E.items()
}

CATEGORIES = {
    "person": "Лица",
    "org_state": "Госорганы РФ",
    "org_bloc": "Блоки и организации",
    "company": "Компании",
    "military": "Военные структуры",
    "media": "Медиа-инструменты",
    "concept": "Концепты и объекты",
}


def get_entity(key: str) -> dict | None:
    return ENTITIES.get((key or "").lower())


def match_confidence(text: str, entity: dict) -> float:
    """worldmonitor-style confidence: direct name > alias."""
    t = (text or "").lower()
    if entity["name_ru"].lower() in t or entity["name_en"].lower() in t:
        return 0.95
    for alias in entity["aliases"]:
        if alias in t:
            return 0.90
    return 0.0


def match_entities(title: str, body: str = "") -> list[str]:
    """Pipeline-time matching: which registry entities does an article mention.

    Lowercased substring match over title + body head; alias lists already
    carry word-boundary guards where needed (e.g. " rt "). Returns entity keys.
    """
    blob = f" {title or ''} {(body or '')[:2500]} ".lower()
    found = []
    for key, e in ENTITIES.items():
        if any(alias in blob for alias in e["aliases"]):
            found.append(key)
    return found
