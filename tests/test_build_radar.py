from __future__ import annotations

import pytest

from scripts.build_radar import parse_args


def test_radar_cli_accepts_only_the_audited_90_day_window():
    assert parse_args(["--days", "90", "--shadow"]).days == 90

    with pytest.raises(SystemExit):
        parse_args(["--days", "89", "--shadow"])
    with pytest.raises(SystemExit):
        parse_args(["--days", "91", "--apply"])
