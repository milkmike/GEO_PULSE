"""Offline all-area monitoring spine, separate from geopolitical RRI countries.

The UN M49 snapshot is vendored, never fetched at runtime. Russia is retained
in the source snapshot but excluded as a foreign monitoring target. Taiwan is
an explicitly marked compatibility row from the existing GEO PULSE registry.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.countries import COUNTRIES


_PATH = Path(__file__).resolve().parents[1] / "data" / "monitoring_countries.json"
_SNAPSHOT = json.loads(_PATH.read_text(encoding="utf-8"))
CATALOG_METADATA = _SNAPSHOT["metadata"]


def _load() -> dict[str, dict]:
    rows = _SNAPSHOT["entries"]
    if len(rows) != CATALOG_METADATA["entry_count"]:
        raise ValueError("monitoring catalog count mismatch")
    result = {}
    for row in rows:
        code = row["code"]
        if (not isinstance(code, str) or len(code) != 2 or not code.isalpha()
                or code != code.upper() or code in result
                or not isinstance(row["iso3"], str) or len(row["iso3"]) != 3
                or not row["name_en"] or not row["name_ru"]):
            raise ValueError("invalid monitoring catalog")
        if code == "RU":
            continue
        existing = COUNTRIES.get(code)
        result[code] = {
            "code": code,
            "name_ru": existing["name_ru"] if existing else row["name_ru"],
            "name_en": existing["name_en"] if existing else row["name_en"],
            "iso3": existing["iso3"] if existing else row["iso3"],
            "m49": row["m49"],
            "region": row["region"],
            "subregion": row["subregion"],
            "catalog_source": row["source"],
        }
    if set(COUNTRIES) - set(result):
        raise ValueError("legacy countries missing from monitoring catalog")
    return dict(sorted(result.items()))


MONITORING_COUNTRIES = _load()


def all_codes() -> list[str]:
    """Every foreign country or area code in deterministic order."""
    return list(MONITORING_COUNTRIES)
