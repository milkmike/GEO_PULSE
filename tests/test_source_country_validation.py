"""Admin source admission uses the same foreign monitoring scope as collectors."""
import pytest
from pydantic import ValidationError

from src.api.routes.sources import SourceCreate, SourceUpdate


@pytest.mark.parametrize('code', ['AD', 'AG', 'RS', 'ET'])
def test_create_source_accepts_worldwide_monitoring_country(code):
    source = SourceCreate(name='Publisher', url='https://example.org/feed', country_code=code.lower())
    assert source.country_code == code


def test_update_country_is_normalized_and_validated():
    assert SourceUpdate(country_code=' ad ').country_code == 'AD'
    assert SourceUpdate().country_code is None
    with pytest.raises(ValidationError):
        SourceUpdate(country_code='ZZ')


@pytest.mark.parametrize('code', ['ZZ', 'RU'])
def test_create_source_rejects_non_foreign_monitoring_country(code):
    with pytest.raises(ValidationError):
        SourceCreate(name='Publisher', url='https://example.org/feed', country_code=code)
