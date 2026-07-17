#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import config  # noqa: E402
from src.collectors.source_candidates import (  # noqa: E402
    configured_publisher_sources,
    fetch_and_validate_candidate,
    load_source_candidates,
    load_production_source_inventory,
    render_validation_markdown,
    validate_candidate_metadata,
    validation_summary,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the isolated national source candidate registry."
    )
    parser.add_argument(
        "--candidate-file",
        type=Path,
        default=config.SOURCE_CANDIDATES_PATH,
    )
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument(
        "--promotion-preflight",
        action="store_true",
        help="Fail closed against a reviewed production source inventory.",
    )
    parser.add_argument(
        "--production-inventory",
        type=Path,
        help="Read-only JSON export of the production sources table.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.promotion_preflight and args.production_inventory is None:
        parser.error("--promotion-preflight requires --production-inventory")
    if args.production_inventory is not None and not args.promotion_preflight:
        parser.error("--production-inventory requires --promotion-preflight")
    if args.promotion_preflight and args.catalog_only:
        parser.error("--promotion-preflight cannot be combined with --catalog-only")

    candidates = load_source_candidates(args.candidate_file)
    configured = configured_publisher_sources(config.load_sources())
    production = (
        load_production_source_inventory(args.production_inventory)
        if args.production_inventory is not None
        else None
    )
    metadata = [
        validate_candidate_metadata(
            candidate,
            configured_publishers=configured,
            production_publishers=production,
        )
        for candidate in candidates
    ]
    if args.catalog_only or any(not result.ok for result in metadata):
        results = metadata
    else:
        with httpx.Client() as client:
            results = [
                fetch_and_validate_candidate(candidate, client=client)
                for candidate in candidates
            ]
    summary = validation_summary(results)
    if args.json:
        payload = {
            "summary": summary,
            "results": [asdict(result) for result in results],
        }
        if args.promotion_preflight:
            payload["mode"] = "promotion_preflight"
        output = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    else:
        output = render_validation_markdown(results)

    if args.out is not None:
        args.out.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    return 0 if summary["total"] > 0 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
