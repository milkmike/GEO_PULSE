#!/usr/bin/env python3
"""Continuously prepare and index bounded story-eligible embeddings."""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from scripts.index_embeddings import index_pending
from scripts.prepare_embedding_jobs import prepare_jobs
from src.db import wait_for_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("embedding-worker")


def _positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def run_embedding_cycle(
    *,
    days: int = 30,
    prepare_limit: int = 500,
    batch_size: int = 50,
    index_limit: int = 500,
    prepare: Callable[..., dict[str, Any]] = prepare_jobs,
    index: Callable[..., dict[str, Any]] = index_pending,
) -> dict[str, object]:
    """Prepare all recent story-eligible articles, then drain a bounded batch."""

    for name, value in (
        ("days", days),
        ("prepare_limit", prepare_limit),
        ("batch_size", batch_size),
        ("index_limit", index_limit),
    ):
        _positive(name, value)

    prepared = prepare(
        days=days,
        limit=prepare_limit,
        story_eligible_articles=True,
    )
    indexed = index(batch_size=batch_size, limit=index_limit)
    return {"prepare": prepared, "index": indexed}


def run_loop(
    *,
    interval: float,
    cycle: Callable[[], dict[str, object]],
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_cycles: int | None = None,
) -> list[dict[str, object]]:
    """Run cycles forever, or a bounded number for deterministic tests."""

    _positive("interval", interval)
    if max_cycles is not None:
        _positive("max_cycles", max_cycles)

    reports: list[dict[str, object]] = []
    cycle_count = 0
    while max_cycles is None or cycle_count < max_cycles:
        started = monotonic()
        try:
            report = cycle()
            logger.info("Embedding cycle: %s", json.dumps(report, sort_keys=True))
        except Exception as exc:
            logger.exception("Embedding cycle failed")
            report = {"error": str(exc)}
        cycle_count += 1
        if max_cycles is not None:
            reports.append(report)

        if max_cycles is not None and cycle_count >= max_cycles:
            break
        elapsed = max(0.0, monotonic() - started)
        sleep(max(0.0, interval - elapsed))
    return reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and index story-eligible article embeddings",
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--prepare-limit", type=int, default=500)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--index-limit", type=int, default=500)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    for name, value in (
        ("interval", args.interval),
        ("days", args.days),
        ("prepare_limit", args.prepare_limit),
        ("batch", args.batch),
        ("index_limit", args.index_limit),
    ):
        _positive(name, value)

    wait_for_db()

    def cycle() -> dict[str, object]:
        return run_embedding_cycle(
            days=args.days,
            prepare_limit=args.prepare_limit,
            batch_size=args.batch,
            index_limit=args.index_limit,
        )

    if args.loop:
        run_loop(interval=args.interval, cycle=cycle)
    else:
        print(json.dumps(cycle(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
