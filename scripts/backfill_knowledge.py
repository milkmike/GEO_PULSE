#!/usr/bin/env python3
"""One-off migration from the curated/legacy entity JSON to canonical tables.

Usage:
    python -m scripts.backfill_knowledge [--batch 250] [--limit 0]

The command never rewrites ``analysis.entities``.  Registry seeding and every
analysis batch run in their own transaction through ``get_session``.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Callable

from src.db import get_session, wait_for_db
from src.knowledge import DEFAULT_EXTRACTOR_VERSION, KnowledgeBackfillService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill-knowledge")


def run_backfill(
    *,
    batch_size: int = 250,
    limit: int = 0,
    session_factory: Callable[[], Any] = get_session,
    service: KnowledgeBackfillService | None = None,
) -> dict[str, int]:
    """Seed the registry and migrate legacy mentions in ordered transactions."""
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    if limit < 0:
        raise ValueError("limit cannot be negative")
    service = service or KnowledgeBackfillService()

    with session_factory() as session:
        entity_count = service.seed_registry(session)
    logger.info("Upserted %d canonical registry entities", entity_count)

    analyses = 0
    mentions = 0
    after_analysis_id = 0
    while not limit or analyses < limit:
        current_batch_size = batch_size
        if limit:
            current_batch_size = min(batch_size, limit - analyses)
        with session_factory() as session:
            batch = service.backfill_batch(
                session,
                after_analysis_id=after_analysis_id,
                batch_size=current_batch_size,
            )
        analyses += batch.analyses_seen
        mentions += batch.mentions_upserted
        after_analysis_id = batch.last_analysis_id
        logger.info(
            "Committed through analysis %d: %d analyses, %d mentions",
            after_analysis_id,
            batch.analyses_seen,
            batch.mentions_upserted,
        )
        if batch.done or batch.analyses_seen == 0:
            break

    return {
        "entities": entity_count,
        "analyses": analyses,
        "mentions": mentions,
        "last_analysis_id": after_analysis_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill canonical knowledge tables")
    parser.add_argument("--batch", type=int, default=250, help="Analyses per transaction")
    parser.add_argument("--limit", type=int, default=0, help="Maximum analyses (0=all)")
    parser.add_argument(
        "--extractor-version",
        default=DEFAULT_EXTRACTOR_VERSION,
        help="Version stored on migrated mentions",
    )
    args = parser.parse_args()

    wait_for_db()
    result = run_backfill(
        batch_size=args.batch,
        limit=args.limit,
        service=KnowledgeBackfillService(args.extractor_version),
    )
    logger.info("Backfill complete: %s", result)


if __name__ == "__main__":
    main()
