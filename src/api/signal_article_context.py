"""Batch article previews for detector signals."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from src.api.public_urls import safe_public_url


def load_signal_article_previews(
    session: Any,
    signal_rows: Any,
    limit: int = 2,
) -> dict[int, dict[str, Any]]:
    """Load persisted evidence, or nearby context, for signal list rows."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    signal_ids = list(dict.fromkeys(int(row.id) for row in signal_rows))
    previews: dict[int, dict[str, Any]] = {
        signal_id: {
            "kind": "unavailable",
            "articles": [],
            "total": 0,
            "window_hours": None,
            "window_start": None,
            "window_end": None,
        }
        for signal_id in signal_ids
    }
    if not signal_ids:
        return previews

    rows = session.execute(
        text(
            """
            WITH requested_base AS (
                SELECT signal.id AS signal_id,
                       signal.signal_type,
                       signal.country_code,
                       signal.created_at,
                       COALESCE(evidence.article_ids, '{}'::integer[]) AS article_ids,
                       evidence.window_start AS evidence_window_start,
                       evidence.window_end AS evidence_window_end,
                       CASE
                           WHEN cardinality(
                                    COALESCE(evidence.article_ids, '{}'::integer[])
                                ) = 0
                                AND signal.country_code IS NOT NULL
                                AND signal.signal_type IN (
                                    'tone_shift', 'volume_surge', 'index_shift'
                                )
                               THEN TRUE
                           ELSE FALSE
                       END AS context_eligible,
                       CASE
                           WHEN signal.signal_type IN ('tone_shift', 'volume_surge')
                                AND gdelt_anchor.day IS NOT NULL
                               THEN gdelt_anchor.day::timestamp AT TIME ZONE 'UTC'
                                    + INTERVAL '1 day'
                           ELSE signal.created_at
                       END AS context_end
                FROM signals signal
                LEFT JOIN signal_evidence evidence
                  ON evidence.signal_id = signal.id
                LEFT JOIN LATERAL (
                    SELECT gdelt.day
                    FROM gdelt_daily gdelt
                    WHERE signal.signal_type IN ('tone_shift', 'volume_surge')
                      AND cardinality(
                            COALESCE(evidence.article_ids, '{}'::integer[])
                          ) = 0
                      AND gdelt.country_code = signal.country_code
                      AND gdelt.day <= (signal.created_at AT TIME ZONE 'UTC')::date
                    ORDER BY
                        CASE
                            WHEN signal.signal_type = 'tone_shift'
                                 AND jsonb_typeof(signal.payload -> 'tone') = 'number'
                                 AND gdelt.tone_avg IS NOT NULL
                                 AND ABS(
                                     gdelt.tone_avg
                                     - CASE
                                           WHEN jsonb_typeof(
                                               signal.payload -> 'tone'
                                           ) = 'number'
                                               THEN (
                                                   signal.payload ->> 'tone'
                                               )::numeric
                                       END
                                 ) <= 0.01
                                THEN 0
                            WHEN signal.signal_type = 'volume_surge'
                                 AND jsonb_typeof(signal.payload -> 'share') = 'number'
                                 AND jsonb_typeof(signal.payload -> 'volume') = 'number'
                                 AND gdelt.volume_share IS NOT NULL
                                 AND gdelt.volume IS NOT NULL
                                 AND ABS(
                                     gdelt.volume_share
                                     - CASE
                                           WHEN jsonb_typeof(
                                               signal.payload -> 'share'
                                           ) = 'number'
                                               THEN (
                                                   signal.payload ->> 'share'
                                               )::numeric
                                       END
                                 ) <= 0.00001
                                 AND ABS(
                                     gdelt.volume
                                     - CASE
                                           WHEN jsonb_typeof(
                                               signal.payload -> 'volume'
                                           ) = 'number'
                                               THEN (
                                                   signal.payload ->> 'volume'
                                               )::numeric
                                       END
                                 ) <= 0.01
                                THEN 0
                            ELSE 1
                        END,
                        gdelt.day DESC
                    LIMIT 1
                ) gdelt_anchor ON TRUE
                WHERE signal.id = ANY(CAST(:signal_ids AS integer[]))
            ), requested AS (
                SELECT requested_base.*,
                       CASE
                           WHEN context_eligible
                               THEN context_end - INTERVAL '72 hours'
                           ELSE NULL
                       END AS context_start
                FROM requested_base
            ), exact_candidates AS (
                SELECT requested.signal_id,
                       ar.id AS article_id,
                       ar.title,
                       COALESCE(NULLIF(ar.resolved_url, ''), ar.url) AS url,
                       ar.published_at,
                       source.name AS source_name,
                       source.country_code,
                       persisted.ordinality AS evidence_ordinality
                FROM requested
                CROSS JOIN LATERAL unnest(requested.article_ids)
                  WITH ORDINALITY AS persisted(article_id, ordinality)
                JOIN articles ar ON ar.id = persisted.article_id
                JOIN article_country_facts source ON source.article_id = ar.id
            ), exact_ranked AS (
                SELECT exact_candidates.*,
                       COUNT(*) OVER (PARTITION BY signal_id) AS total,
                       ROW_NUMBER() OVER (
                           PARTITION BY signal_id
                           ORDER BY evidence_ordinality ASC NULLS LAST,
                                    article_id
                       ) AS candidate_rank
                FROM exact_candidates
            ), exact_previews AS (
                SELECT signal_id, total, candidate_rank, article_id,
                       title, url, published_at, source_name, country_code
                FROM exact_ranked
                WHERE candidate_rank <= :lim
            ), context_windows AS MATERIALIZED (
                SELECT DISTINCT country_code, context_start, context_end
                FROM requested
                WHERE context_eligible
            ), context_pool_bounds AS (
                SELECT MIN(context_start) AS global_start,
                       MAX(context_end) AS global_end,
                       ARRAY_AGG(DISTINCT country_code) AS country_codes
                FROM context_windows
            ), context_article_pool AS MATERIALIZED (
                SELECT ar.id AS article_id,
                       ar.title,
                       COALESCE(NULLIF(ar.resolved_url, ''), ar.url) AS url,
                       ar.published_at,
                       source.name AS source_name,
                       source.country_code,
                       analysis.action_level AS analysis_action_level,
                       ABS(analysis.sentiment) AS absolute_sentiment,
                       ar.reprint_count
                FROM context_pool_bounds
                JOIN articles ar ON TRUE
                JOIN article_country_facts source ON source.article_id = ar.id
                JOIN analysis analysis ON analysis.article_id = ar.id
                WHERE context_pool_bounds.global_start IS NOT NULL
                  AND source.country_code = ANY(context_pool_bounds.country_codes)
                  AND ar.published_at >= context_pool_bounds.global_start
                  AND ar.published_at < context_pool_bounds.global_end
                  AND ar.is_duplicate = FALSE
                  AND analysis.is_relevant = TRUE
            ), context_candidates AS (
                SELECT context_windows.country_code AS window_country_code,
                       context_windows.context_start,
                       context_windows.context_end,
                       context_article_pool.article_id,
                       context_article_pool.title,
                       context_article_pool.url,
                       context_article_pool.published_at,
                       context_article_pool.source_name,
                       context_article_pool.country_code,
                       context_article_pool.analysis_action_level,
                       context_article_pool.absolute_sentiment,
                       context_article_pool.reprint_count
                FROM context_windows
                JOIN context_article_pool
                  ON context_article_pool.country_code = context_windows.country_code
                 AND context_article_pool.published_at >= context_windows.context_start
                 AND context_article_pool.published_at < context_windows.context_end
            ), context_ranked AS (
                SELECT context_candidates.*,
                       COUNT(*) OVER (
                           PARTITION BY window_country_code,
                                        context_start,
                                        context_end
                       ) AS total,
                       ROW_NUMBER() OVER (
                           PARTITION BY window_country_code,
                                        context_start,
                                        context_end
                           ORDER BY analysis_action_level DESC NULLS LAST,
                                    absolute_sentiment DESC NULLS LAST,
                                    reprint_count DESC NULLS LAST,
                                    published_at DESC NULLS LAST,
                                    article_id ASC
                       ) AS candidate_rank
                FROM context_candidates
            ), context_top AS MATERIALIZED (
                SELECT *
                FROM context_ranked
                WHERE candidate_rank <= :lim
            ), context_previews AS (
                SELECT requested.signal_id,
                       context_top.total,
                       context_top.candidate_rank,
                       context_top.article_id,
                       context_top.title,
                       context_top.url,
                       context_top.published_at,
                       context_top.source_name,
                       context_top.country_code
                FROM requested
                JOIN context_top
                  ON context_top.window_country_code = requested.country_code
                 AND context_top.context_start = requested.context_start
                 AND context_top.context_end = requested.context_end
                WHERE requested.context_eligible
            ), preview_rows AS (
                SELECT * FROM exact_previews
                UNION ALL
                SELECT * FROM context_previews
            )
            SELECT requested.signal_id,
                   CASE
                       WHEN cardinality(requested.article_ids) > 0
                           THEN 'evidence'
                       WHEN requested.context_eligible
                           THEN 'context'
                       ELSE 'unavailable'
                   END AS kind,
                   COALESCE(preview_rows.total, 0) AS total,
                   CASE
                       WHEN cardinality(requested.article_ids) = 0
                            AND requested.context_eligible
                           THEN 72
                       ELSE NULL
                   END AS window_hours,
                   CASE
                       WHEN cardinality(requested.article_ids) > 0
                           THEN requested.evidence_window_start
                       WHEN requested.context_eligible
                           THEN requested.context_start
                       ELSE NULL
                   END AS window_start,
                   CASE
                       WHEN cardinality(requested.article_ids) > 0
                           THEN requested.evidence_window_end
                       WHEN requested.context_eligible
                           THEN requested.context_end
                       ELSE NULL
                   END AS window_end,
                   preview_rows.article_id, preview_rows.title,
                   preview_rows.url, preview_rows.published_at,
                   preview_rows.source_name, preview_rows.country_code
            FROM requested
            LEFT JOIN preview_rows
              ON preview_rows.signal_id = requested.signal_id
            ORDER BY requested.signal_id,
                     preview_rows.candidate_rank NULLS LAST
            """
        ),
        {"signal_ids": signal_ids, "lim": limit},
    ).fetchall()

    for row in rows:
        signal_id = int(row.signal_id)
        preview = previews.get(signal_id)
        if preview is None:
            continue
        preview["kind"] = row.kind
        preview["total"] = int(row.total or 0)
        preview["window_hours"] = (
            int(row.window_hours) if row.window_hours is not None else None
        )
        preview["window_start"] = (
            row.window_start.isoformat() if row.window_start else None
        )
        preview["window_end"] = row.window_end.isoformat() if row.window_end else None
        if row.article_id is None:
            continue
        preview["articles"].append(
            {
                "id": int(row.article_id),
                "title": row.title,
                "url": safe_public_url(row.url),
                "published_at": (
                    row.published_at.isoformat() if row.published_at else None
                ),
                "source_name": row.source_name,
                "country_code": row.country_code,
            }
        )

    return previews
