"""Shared deterministic matching for bounded Radar episodes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache


MAX_CONTOUR_GAP = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class EpisodeInterval:
    item_id: int
    first_observed_at: datetime
    last_observed_at: datetime
    stable_key: str


def episode_distance(left, right) -> timedelta:
    """Return zero for overlap, otherwise the gap between two episodes."""

    left_end = left.last_observed_at or left.first_observed_at
    right_end = right.last_observed_at or right.first_observed_at
    if left.first_observed_at <= right_end and right.first_observed_at <= left_end:
        return timedelta()
    if left_end < right.first_observed_at:
        return right.first_observed_at - left_end
    return left.first_observed_at - right_end


def match_episode_intervals(
    left_members: list[EpisodeInterval],
    right_members: list[EpisodeInterval],
) -> tuple[tuple[int, int], ...]:
    """Return the maximum order-preserving one-to-one match within 14 days."""

    left = tuple(sorted(
        left_members,
        key=lambda item: (item.first_observed_at, item.stable_key, item.item_id),
    ))
    right = tuple(sorted(
        right_members,
        key=lambda item: (item.first_observed_at, item.stable_key, item.item_id),
    ))

    def score(pairs: tuple[tuple[int, int], ...]) -> tuple[object, ...]:
        gap_seconds = sum(
            episode_distance(left[i], right[j]).total_seconds()
            for i, j in pairs
        )
        identity = tuple(
            (
                left[i].stable_key, str(left[i].item_id),
                right[j].stable_key, str(right[j].item_id),
            )
            for i, j in pairs
        )
        return -len(pairs), gap_seconds, identity

    @lru_cache(maxsize=None)
    def solve(i: int, j: int) -> tuple[tuple[int, int], ...]:
        if i == len(left) or j == len(right):
            return ()
        options = [solve(i + 1, j), solve(i, j + 1)]
        if episode_distance(left[i], right[j]) <= MAX_CONTOUR_GAP:
            options.append(((i, j),) + solve(i + 1, j + 1))
        return min(options, key=score)

    return tuple(
        (left[i].item_id, right[j].item_id)
        for i, j in solve(0, 0)
    )
