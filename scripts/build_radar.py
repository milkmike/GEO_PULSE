"""Build a replayable Early Warning Radar snapshot.

Writes are opt-in.  Invoking this script without ``--apply`` is always a
shadow replay, which makes it safe for backtests and operational dry-runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ``python scripts/build_radar.py`` is the documented operational invocation;
# make the repository package available without requiring an external wrapper.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_session, wait_for_db
from src.radar.service import run_radar_cycle


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
    parser.add_argument("--days", type=int, default=90, help="lookback window (default: 90)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", action="store_true", help="dry-run (the default)")
    mode.add_argument("--apply", action="store_true", help="persist immutable observations and trends")
    parser.add_argument("--json-report", help="write the machine-readable replay report")
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = _as_of(args.as_of)
    # Explicit --shadow is retained for audit logs; absence still means shadow.
    shadow = not args.apply
    wait_for_db()
    with get_session() as session:
        report = run_radar_cycle(session, as_of, shadow=shadow, days=args.days)
        if args.apply:
            session.commit()
    payload = report.json_report()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_report:
        Path(args.json_report).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
