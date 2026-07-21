"""Build a replayable Early Warning Radar snapshot.

Writes are opt-in.  Invoking this script without ``--apply`` is always a
shadow replay, which makes it safe for backtests and operational dry-runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

# ``python scripts/build_radar.py`` is the documented operational invocation;
# make the repository package available without requiring an external wrapper.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("radar-worker")
RADAR_LOCK_KEY = "geopulse-radar-worker"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed

def _as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build replayable Radar waves")
    parser.add_argument("--as-of", help="ISO-8601 UTC cutoff (defaults to now)")
    parser.add_argument("--days", type=int, default=90, help="audited lookback window (must be 90)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", action="store_true", help="dry-run (the default)")
    mode.add_argument("--apply", action="store_true", help="persist immutable observations and trends")
    parser.add_argument("--loop", action="store_true", help="run recurring cycles")
    parser.add_argument("--interval", type=_positive_int, default=3600)
    parser.add_argument("--json-report", help="write the machine-readable replay report")
    args = parser.parse_args(argv)
    if args.days != 90:
        parser.error("--days must be exactly 90 for Radar Wave 1")
    if args.loop and args.as_of:
        parser.error("--as-of cannot be fixed in loop mode")
    if args.loop and args.json_report:
        parser.error("--json-report is only available for one-shot runs")
    return args


def _acquire_apply_lock(session: Any) -> bool:
    return bool(
        session.execute(
            text(
                "SELECT pg_try_advisory_xact_lock(hashtext(:lock_key))"
            ),
            {"lock_key": RADAR_LOCK_KEY},
        ).scalar()
    )


def run_once(
    args: argparse.Namespace,
    *,
    now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    session_factory: Callable[[], Any] | None = None,
    cycle_runner: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Run one replay-safe Radar cycle and return its JSON report."""

    if session_factory is None:
        from src.db import get_session

        session_factory = get_session
    if cycle_runner is None:
        from src.radar.service import run_radar_cycle

        cycle_runner = run_radar_cycle

    as_of = _as_of(args.as_of) if args.as_of else now_factory()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Radar cycle time must include a timezone")
    as_of = as_of.astimezone(timezone.utc)
    # Explicit --shadow is retained for audit logs; absence still means shadow.
    shadow = not args.apply
    with session_factory() as session:
        if not shadow and not _acquire_apply_lock(session):
            return {
                "as_of": as_of.isoformat(),
                "shadow": False,
                "skipped": "lock_not_acquired",
            }
        report = cycle_runner(session, as_of, shadow=shadow, days=args.days)
        if args.apply:
            session.commit()
    return report.json_report()


def run_loop(
    args: argparse.Namespace,
    *,
    run_once_fn: Callable[[argparse.Namespace], dict[str, object]] = run_once,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_cycles: int | None = None,
) -> list[dict[str, object]]:
    """Run Radar with a fresh ``as_of`` for every scheduled cycle."""

    if args.interval <= 0:
        raise ValueError("interval must be positive")
    if max_cycles is not None and max_cycles <= 0:
        raise ValueError("max_cycles must be positive")

    reports: list[dict[str, object]] = []
    cycle_count = 0
    while max_cycles is None or cycle_count < max_cycles:
        started = monotonic()
        try:
            payload = run_once_fn(args)
        except Exception as exc:
            logger.exception("Radar cycle failed")
            payload = {"shadow": not args.apply, "error": str(exc)}
        cycle_count += 1
        if max_cycles is not None:
            reports.append(payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

        if max_cycles is not None and cycle_count >= max_cycles:
            break
        elapsed = max(0.0, monotonic() - started)
        sleep(max(0.0, args.interval - elapsed))
    return reports


def _render_report(payload: dict[str, object], output_path: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output_path:
        Path(output_path).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Keep --help and argument validation independent of runtime DB config.
    # Importing src.db creates the SQLAlchemy engine, so it belongs after
    # argparse has had a chance to exit successfully.
    from src.db import wait_for_db

    wait_for_db()
    if args.loop:
        run_loop(args)
    else:
        _render_report(run_once(args), args.json_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
