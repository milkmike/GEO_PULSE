from __future__ import annotations

import os
import gc
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import weakref

import pytest
import yaml

from scripts.build_radar import parse_args, run_loop, run_once


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


def test_radar_cli_accepts_positive_loop_interval_and_rejects_fixed_replay_time():
    args = parse_args(["--apply", "--loop", "--interval", "3600"])

    assert args.loop is True
    assert args.interval == 3600

    with pytest.raises(SystemExit):
        parse_args(["--apply", "--loop", "--interval", "0"])
    with pytest.raises(SystemExit):
        parse_args([
            "--apply",
            "--loop",
            "--as-of",
            "2026-07-21T12:00:00Z",
        ])


class _LockResult:
    def __init__(self, acquired: bool):
        self.acquired = acquired

    def scalar(self):
        return self.acquired


class _Session:
    def __init__(self, *, acquired: bool = True):
        self.acquired = acquired
        self.commits = 0
        self.statements: list[str] = []

    def execute(self, statement, _params=None):
        self.statements.append(str(statement))
        return _LockResult(self.acquired)

    def commit(self):
        self.commits += 1


def _session_factory(session):
    @contextmanager
    def factory():
        yield session

    return factory


class _Report:
    def __init__(self, as_of, *, shadow):
        self.as_of = as_of
        self.shadow = shadow

    def json_report(self):
        return {
            "as_of": self.as_of.isoformat(),
            "shadow": self.shadow,
        }


def test_shadow_cycle_never_locks_or_commits():
    args = parse_args(["--shadow"])
    session = _Session()
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)

    payload = run_once(
        args,
        now_factory=lambda: now,
        session_factory=_session_factory(session),
        cycle_runner=lambda _session, as_of, **options: _Report(
            as_of,
            shadow=options["shadow"],
        ),
    )

    assert payload == {"as_of": now.isoformat(), "shadow": True}
    assert session.statements == []
    assert session.commits == 0


def test_apply_cycle_owns_advisory_lock_and_commits_once():
    args = parse_args(["--apply"])
    session = _Session(acquired=True)
    now = datetime(2026, 7, 21, 13, tzinfo=timezone.utc)

    payload = run_once(
        args,
        now_factory=lambda: now,
        session_factory=_session_factory(session),
        cycle_runner=lambda _session, as_of, **options: _Report(
            as_of,
            shadow=options["shadow"],
        ),
    )

    assert payload == {"as_of": now.isoformat(), "shadow": False}
    assert any("pg_try_advisory_xact_lock" in sql for sql in session.statements)
    assert session.commits == 1


def test_apply_cycle_skips_when_another_worker_owns_the_lock():
    args = parse_args(["--apply"])
    session = _Session(acquired=False)
    called = []

    payload = run_once(
        args,
        now_factory=lambda: datetime(2026, 7, 21, 14, tzinfo=timezone.utc),
        session_factory=_session_factory(session),
        cycle_runner=lambda *_args, **_kwargs: called.append(True),
    )

    assert payload["shadow"] is False
    assert payload["skipped"] == "lock_not_acquired"
    assert called == []
    assert session.commits == 0


def test_radar_loop_uses_a_fresh_as_of_for_each_cycle():
    args = parse_args(["--apply", "--loop", "--interval", "3600"])
    times = iter((
        datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
        datetime(2026, 7, 21, 13, tzinfo=timezone.utc),
    ))
    seen = []
    ticks = iter((0.0, 2.0, 3600.0, 3603.0))

    def one_cycle(loop_args):
        session = _Session()
        return run_once(
            loop_args,
            now_factory=lambda: next(times),
            session_factory=_session_factory(session),
            cycle_runner=lambda _session, as_of, **options: seen.append(as_of)
            or _Report(as_of, shadow=options["shadow"]),
        )

    reports = run_loop(
        args,
        run_once_fn=one_cycle,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(ticks),
        max_cycles=2,
    )

    assert seen == [
        datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
        datetime(2026, 7, 21, 13, tzinfo=timezone.utc),
    ]
    assert [report["shadow"] for report in reports] == [False, False]


def test_unbounded_radar_loop_does_not_retain_prior_reports():
    args = parse_args(["--apply", "--loop", "--interval", "3600"])

    class Report(dict):
        pass

    class StopLoop(Exception):
        pass

    refs = []

    def one_cycle(_args):
        report = Report(shadow=False, sequence=len(refs) + 1)
        refs.append(weakref.ref(report))
        return report

    def stop_after_second_cycle(_seconds):
        if len(refs) < 2:
            return
        gc.collect()
        assert refs[0]() is None
        raise StopLoop

    with pytest.raises(StopLoop):
        run_loop(
            args,
            run_once_fn=one_cycle,
            sleep=stop_after_second_cycle,
            monotonic=iter((0.0, 1.0, 3600.0, 3601.0)).__next__,
        )


def test_production_radar_worker_is_persisted_hourly_and_always_enabled():
    root = Path(__file__).resolve().parents[1]
    worker = yaml.safe_load((root / "docker-compose.yml").read_text())[
        "services"
    ]["radar-worker"]

    assert worker["command"] == (
        "python scripts/build_radar.py --apply --days 90 "
        "--loop --interval 3600"
    )
    assert "profiles" not in worker
    assert worker["restart"] == "unless-stopped"
