"""Unknown native bills stay unknown rather than looking like free calls."""
from decimal import Decimal
from unittest.mock import Mock

import psycopg2
import pytest

from src import api_tracker as tracker


@pytest.mark.parametrize('actual,estimate,expected', [
    (None, False, None), (None, True, .125), (.01, False, .01)])
def test_explicit_unknown_cost_is_persisted_as_null(monkeypatch, actual, estimate, expected):
    connection = Mock()
    cursor = connection.cursor.return_value
    monkeypatch.setattr(psycopg2, 'connect', lambda **kwargs: connection)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@127.0.0.1:1/test')
    calculate = Mock(return_value=Decimal('.125'))
    monkeypatch.setattr(tracker, 'calculate_cost', calculate)
    tracker.track_api_call(service='deepseek', model='deepseek-flash', tokens_in=10,
        tokens_out=20, cost=actual, estimate_missing_cost=estimate)
    params = cursor.execute.call_args.args[1]
    assert params[4:7] == (10, 20, expected)
    assert calculate.call_count == (1 if actual is None and estimate else 0)
    connection.commit.assert_called_once()
