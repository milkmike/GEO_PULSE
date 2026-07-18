from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.build_radar import parse_args


def test_radar_cli_accepts_only_the_audited_90_day_window():
    assert parse_args(["--days", "90", "--shadow"]).days == 90

    with pytest.raises(SystemExit):
        parse_args(["--days", "89", "--shadow"])
    with pytest.raises(SystemExit):
        parse_args(["--days", "91", "--apply"])


def test_radar_cli_help_does_not_require_database_configuration():
    root = Path(__file__).resolve().parents[1]
    environment = {
        key: value for key, value in os.environ.items()
        if key != "DATABASE_URL" and not key.startswith("PG")
    }
    environment["DATABASE_URL"] = ""

    result = subprocess.run(
        [sys.executable, str(root / "scripts/build_radar.py"), "--help"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Build replayable Radar waves" in result.stdout
