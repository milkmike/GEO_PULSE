"""Configuration management."""
import os
from pathlib import Path

import yaml

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://thermo:thermo@localhost:5432/cis_thermometer")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
API_URL = os.environ.get("API_URL", "http://localhost:8000")

# Paths
BASE_DIR = Path(__file__).parent.parent
SOURCES_PATH = BASE_DIR / "src" / "collectors" / "sources.yaml"

# Country names
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
    """Load sources configuration from YAML."""
    with open(SOURCES_PATH) as f:
        return yaml.safe_load(f)
