"""The all-area monitor must not shrink to the historical 99-country RRI set."""
from src.countries import COUNTRIES
from src.monitoring_registry import CATALOG_METADATA, MONITORING_COUNTRIES, all_codes


def test_offline_un_catalog_is_complete_and_preserves_legacy_names():
    assert len(MONITORING_COUNTRIES) == 248
    assert CATALOG_METADATA["entry_count"] == 249  # 248 UN rows plus marked TW fallback.
    assert CATALOG_METADATA["source_url"] == "https://unstats.un.org/unsd/methodology/m49/overview/"
    assert "RU" not in MONITORING_COUNTRIES
    assert set(COUNTRIES) <= set(MONITORING_COUNTRIES)
    for code, old in COUNTRIES.items():
        assert MONITORING_COUNTRIES[code]["name_ru"] == old["name_ru"]
        assert MONITORING_COUNTRIES[code]["name_en"] == old["name_en"]
        assert MONITORING_COUNTRIES[code]["iso3"] == old["iso3"]


def test_new_areas_and_taiwan_have_explicit_provenance():
    assert MONITORING_COUNTRIES["TW"]["catalog_source"] == "existing_registry_fallback"
    assert MONITORING_COUNTRIES["TW"]["m49"] is None
    for code in ("DJ", "RW", "CD", "FJ", "VA"):
        assert MONITORING_COUNTRIES[code]["catalog_source"] == "UN_M49"
        assert MONITORING_COUNTRIES[code]["name_ru"]
        assert len(MONITORING_COUNTRIES[code]["iso3"]) == 3
    assert all_codes() == sorted(all_codes())
