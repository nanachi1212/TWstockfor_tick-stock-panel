"""Purged, embargoed walk-forward folds over Taiwan trading sessions.

Everything is indexed by **trading session**, never by calendar timedelta. A
"5-day forward label" means five sessions; across a Lunar New Year break that is
not five calendar days, and treating it as such silently leaks.

Why purging is not optional
---------------------------
A feature row dated ``t`` carries a label computed over ``(t, t + horizon]``. If
any part of that window falls inside validation, the training row already knows
something about validation. So the last ``horizon`` sessions of every training
block are removed — *purged* — and a further ``embargo_sessions`` are dropped
after the boundary so that serial correlation immediately across the seam does
not reintroduce the same leak. The same treatment applies at the
validation → test boundary.

Test isolation
--------------
The test block is emitted for scoring only. Feature selection, weights,
hyper-parameters and thresholds must all be decided from train + validation;
``PurgedFold.fittable_sessions`` exists so a caller can assert exactly that.

This module is a generator and a contract. It does not read the census, does
not fit anything, and produces no performance numbers.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

DEFAULT_TRAIN_SESSIONS = 756
DEFAULT_VAL_SESSIONS = 126
DEFAULT_TEST_SESSIONS = 63
DEFAULT_STEP_SESSIONS = 63
DEFAULT_LABEL_HORIZON = 5
DEFAULT_EMBARGO_SESSIONS = 5


@dataclass(frozen=True)
class FoldConfig:
    """Walk-forward geometry, all in trading sessions."""

    train_sessions: int = DEFAULT_TRAIN_SESSIONS
    val_sessions: int = DEFAULT_VAL_SESSIONS
    test_sessions: int = DEFAULT_TEST_SESSIONS
    step_sessions: int = DEFAULT_STEP_SESSIONS
    label_horizon: int = DEFAULT_LABEL_HORIZON
    embargo_sessions: int = DEFAULT_EMBARGO_SESSIONS

    def __post_init__(self) -> None:
        for name in ("train_sessions", "val_sessions", "test_sessions", "step_sessions"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("label_horizon", "embargo_sessions"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")

    @property
    def purge_sessions(self) -> int:
        """Sessions removed from a block's tail so its labels cannot see ahead."""
        return self.label_horizon

    @property
    def window_sessions(self) -> int:
        """Total sessions one fold consumes, gaps included."""
        return (
            self.train_sessions
            + self.purge_sessions + self.embargo_sessions
            + self.val_sessions
            + self.purge_sessions + self.embargo_sessions
            + self.test_sessions
        )

    def describe(self) -> dict[str, Any]:
        return {
            "train_sessions": self.train_sessions,
            "val_sessions": self.val_sessions,
            "test_sessions": self.test_sessions,
            "step_sessions": self.step_sessions,
            "label_horizon": self.label_horizon,
            "embargo_sessions": self.embargo_sessions,
            "purge_sessions": self.purge_sessions,
            "window_sessions": self.window_sessions,
        }


@dataclass(frozen=True)
class PurgedFold:
    """One walk-forward fold, with its boundaries as concrete sessions."""

    index: int
    train_start: date
    train_end: date
    val_start: date
    val_end: date
    test_start: date
    test_end: date
    purge_sessions: int
    embargo_sessions: int
    label_horizon: int
    confidence_degraded: bool = False
    degraded_reason: str | None = None

    @property
    def fittable_sessions(self) -> tuple[date, date, date, date]:
        """The only sessions a model may learn from: train + validation."""
        return (self.train_start, self.train_end, self.val_start, self.val_end)

    def contains_leak(self) -> bool:
        """True if any block overlaps, or a gap is too small to purge the labels."""
        return not (
            self.train_start <= self.train_end < self.val_start <= self.val_end
            < self.test_start <= self.test_end
        )

    def describe(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "val_start": self.val_start.isoformat(),
            "val_end": self.val_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "purge_sessions": self.purge_sessions,
            "embargo_sessions": self.embargo_sessions,
            "label_horizon": self.label_horizon,
            "confidence_degraded": self.confidence_degraded,
            "degraded_reason": self.degraded_reason,
        }


def generate_folds(
    sessions: Sequence[date],
    config: FoldConfig | None = None,
    *,
    min_coverage_sessions: int | None = None,
) -> list[PurgedFold]:
    """Build every complete fold that fits inside *sessions*.

    *sessions* must be the ordered list of trading sessions — the real calendar,
    not a synthetic date range. Gaps between consecutive entries are exactly
    what makes session indexing correct across holidays.

    A short history yields **no folds**, never a squeezed one: silently
    shrinking the windows would make two runs incomparable. Use
    ``min_coverage_sessions`` to mark folds ``confidence_degraded`` when the
    underlying data coverage is known to be partial.
    """
    cfg = config or FoldConfig()
    ordered = list(sessions)
    if ordered != sorted(ordered):
        raise ValueError("sessions must be sorted ascending")
    if len(set(ordered)) != len(ordered):
        raise ValueError("sessions must not contain duplicates")

    folds: list[PurgedFold] = []
    gap = cfg.purge_sessions + cfg.embargo_sessions
    total = len(ordered)
    start = 0
    index = 0
    while start + cfg.window_sessions <= total:
        train_lo = start
        train_hi = train_lo + cfg.train_sessions - 1
        val_lo = train_hi + 1 + gap
        val_hi = val_lo + cfg.val_sessions - 1
        test_lo = val_hi + 1 + gap
        test_hi = test_lo + cfg.test_sessions - 1

        degraded = False
        reason: str | None = None
        if min_coverage_sessions is not None and test_hi + 1 > min_coverage_sessions:
            degraded = True
            reason = (
                f"fold extends to session {test_hi + 1} but only "
                f"{min_coverage_sessions} sessions have verified coverage"
            )

        folds.append(PurgedFold(
            index=index,
            train_start=ordered[train_lo], train_end=ordered[train_hi],
            val_start=ordered[val_lo], val_end=ordered[val_hi],
            test_start=ordered[test_lo], test_end=ordered[test_hi],
            purge_sessions=cfg.purge_sessions,
            embargo_sessions=cfg.embargo_sessions,
            label_horizon=cfg.label_horizon,
            confidence_degraded=degraded,
            degraded_reason=reason,
        ))
        index += 1
        start += cfg.step_sessions
    return folds


def purged_training_sessions(
    sessions: Sequence[date], fold: PurgedFold
) -> list[date]:
    """Training sessions whose label window cannot reach into validation.

    A row dated ``t`` is dropped when ``t + label_horizon`` would land on or
    after ``val_start``.
    """
    ordered = list(sessions)
    position = {day: i for i, day in enumerate(ordered)}
    val_index = position[fold.val_start]
    return [
        day for day in ordered
        if fold.train_start <= day <= fold.train_end
        and position[day] + fold.label_horizon < val_index
    ]


def leaks_into(fold: PurgedFold, sessions: Sequence[date], feature_date: date) -> bool:
    """Whether a feature row's label window reaches validation or test."""
    position = {day: i for i, day in enumerate(sessions)}
    if feature_date not in position:
        raise KeyError(feature_date)
    label_end = position[feature_date] + fold.label_horizon
    return label_end >= position[fold.val_start]


def iter_fold_blocks(fold: PurgedFold, sessions: Sequence[date]) -> Iterator[tuple[str, date]]:
    """Yield ``(block, session)`` for every session inside the fold."""
    for day in sessions:
        if fold.train_start <= day <= fold.train_end:
            yield "train", day
        elif fold.val_start <= day <= fold.val_end:
            yield "validation", day
        elif fold.test_start <= day <= fold.test_end:
            yield "test", day
