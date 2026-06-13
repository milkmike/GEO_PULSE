"""Configuration management."""
import os
from pathlib import Path

import yaml

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:thermo@localhost:5432/cis_thermometer")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
API_URL = os.environ.get("API_URL", "http://localhost:8000")

# Heavy / structured-output model for batch & clustering jobs (thread dedup,
# reanalyze, digests). The high-volume analyzer/briefs chain lives in
# src/llm.py (LLM_MODELS); this is the single knob for the few scripts that
# need a strong JSON-capable model. Default is a cheap Chinese model — switch
# in one line. Alternatives: deepseek/deepseek-v3.2, xiaomi/mimo-v2.5,
# moonshotai/kimi-k2.6, deepseek/deepseek-v4-flash.
HEAVY_MODEL = os.environ.get("HEAVY_MODEL", "xiaomi/mimo-v2.5-pro")

# Paths
BASE_DIR = Path(__file__).parent.parent
SOURCES_PATH = BASE_DIR / "src" / "collectors" / "sources.yaml"
WORLD_SOURCES_PATH = BASE_DIR / "src" / "collectors" / "sources_world.yaml"

# Country names — tier-1 deep-coverage set (CIS). For the full world registry
# (99 countries, regions, memberships) see src/countries.py.
COUNTRY_NAMES = {
    "KZ": "Казахстан",
    "AM": "Армения",
    "UZ": "Узбекистан",
    "KG": "Кыргызстан",
    "TJ": "Таджикистан",
    "TM": "Туркменистан",
    "AZ": "Азербайджан",
    "GE": "Грузия",
    "MD": "Молдова",
    "BY": "Беларусь",
}

# ISO 3166-1 alpha-3 for plotly
COUNTRY_ISO3 = {
    "KZ": "KAZ", "AM": "ARM", "UZ": "UZB", "KG": "KGZ",
    "TJ": "TJK", "TM": "TKM", "AZ": "AZE", "GE": "GEO",
    "MD": "MDA", "BY": "BLR",
}

EVENT_TYPE_WEIGHTS = {
    "military": 1.5,
    "diplomatic": 1.3,
    "security": 1.2,
    "economic": 1.0,
    "cultural": 0.8,
    None: 1.0,
}


def load_sources() -> dict:
    """Load sources configuration from YAML (CIS + world catalogs merged)."""
    with open(SOURCES_PATH) as f:
        config = yaml.safe_load(f)

    if WORLD_SOURCES_PATH.exists():
        with open(WORLD_SOURCES_PATH) as f:
            world = yaml.safe_load(f) or {}
        for code, data in (world.get("countries") or {}).items():
            if code in config["countries"]:
                config["countries"][code]["sources"].extend(data.get("sources", []))
            else:
                config["countries"][code] = data

    return config
