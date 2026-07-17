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
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    candidates = load_source_candidates(args.candidate_file)
    configured = configured_publisher_sources(config.load_sources())
    metadata = [
        validate_candidate_metadata(candidate, configured_publishers=configured)
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
        output = json.dumps(
            {
                "summary": summary,
                "results": [asdict(result) for result in results],
            },
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
