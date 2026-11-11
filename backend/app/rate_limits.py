"""Shared process-local batch and request pacing helpers."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TypeVar

from app.capabilities import Cap, CapabilitySet

T = TypeVar("T")

# Reserve 20% of a source's declared request budget for other local consumers.
SAFETY_RPM_FACTOR = 0.8

# 進程級共享限速器: 按 rpm 分桶的「下一個可用時刻」表 (Lock 守護)。
# 限制: sleep_between_batches(index=0) 只登記槽位、不 sleep, 因此多個呼叫方若同時以
# index=0 啟動, 仍可能瞬時突發超過單能力 rpm。後續 index>0 批次會按同一時間軸排隊。
# Phase 1 / Stage A: 不要併發啟動多個 probe 或大批量 sync; 跨進程仍靠錯峰, 非帳戶級限頻。
_slot_lock = threading.Lock()
# Buckets use an rpm integer or a source name such as "taiwan:twse".
_next_slot: dict[object, float] = {}


def apply_safety_rpm(rpm: int | None, *, factor: float = SAFETY_RPM_FACTOR) -> int | None:
    """Scale a package rpm by the shared safety factor (default 80%)."""
    if rpm is None or rpm <= 0:
        return rpm
    return max(1, int(rpm * factor))


def _reserve_slot(rpm: int, interval: float, key: object | None = None) -> float:
    """在共享時間軸上為一次請求預約一個發包槽, 返回需等待的秒數 (>=0)。

    interval = 60/rpm。now 早於該 rpm 桶的 next_slot 時排到 next_slot, 否則排到 now;
    隨後把該桶 next_slot 後移 interval。持鎖僅做時間賬目, 不在鎖內 sleep。
    """
    if key is None:
        key = rpm if rpm and rpm > 0 else -1
    with _slot_lock:
        now = time.monotonic()
        scheduled = max(now, _next_slot.get(key, now))
        _next_slot[key] = scheduled + interval
        return scheduled - now


@dataclass(frozen=True)
class ResolvedLimit:
    batch: int | None
    rpm: int | None


def resolve_limit(
    capset: CapabilitySet,
    cap: Cap,
    *,
    default_batch: int | None = None,
    default_rpm: int | None = None,
    default_rpm_when_unset: bool = True,
    apply_safety: bool = True,
) -> ResolvedLimit:
    """Return a capability's batch/rpm with caller-provided fallbacks.

    By default rpm is scaled by SAFETY_RPM_FACTOR (0.8) inside this process only.
    This is not a cross-container account budget. Pass apply_safety=False for diagnostics.
    """
    lim = capset.limits(cap)
    if lim is None:
        rpm = default_rpm
    else:
        rpm = lim.rpm if lim.rpm else (default_rpm if default_rpm_when_unset else None)
        default_batch = lim.batch if lim.batch else default_batch
    if apply_safety:
        rpm = apply_safety_rpm(rpm)
    if lim is None:
        return ResolvedLimit(batch=default_batch, rpm=rpm)
    return ResolvedLimit(
        batch=default_batch,
        rpm=rpm,
    )


def batch_interval(rpm: int | None, *, default: float = 0.0) -> float:
    """Return the existing uniform batch interval formula: 60 / rpm."""
    return 60.0 / rpm if rpm and rpm > 0 else default


def chunked(items: list[T], batch_size: int | None) -> list[list[T]]:
    """Split items by batch size, preserving the existing None-as-one-batch behavior."""
    if batch_size is None:
        return [items]
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def sleep_between_batches(index: int, rpm: int | None, *, default_interval: float = 0.0) -> None:
    """Pace batches via the process-local shared slot table.

    - index == 0: reserve a slot but do **not** sleep (documented first-batch burst).
    - index > 0: wait until the reserved slot time.

    Concurrent callers that all pass index=0 can still burst above rpm; only later
    batches and single-pipeline callers get full spacing. Do not launch multiple
    Phase 1 probes/syncs at once during Stage A.
    """
    interval = batch_interval(rpm, default=default_interval)
    if interval <= 0:
        return
    if index <= 0:
        # First batch: reserve only (no sleep). Concurrent index=0 calls may burst.
        _reserve_slot(rpm or -1, interval)
        return
    wait = _reserve_slot(rpm or -1, interval)
    if wait > 0:
        time.sleep(wait)


def min_batch(preferred: int, limit: ResolvedLimit) -> int:
    """Clamp a user-preferred batch size by a resolved capability batch limit."""
    return min(preferred, limit.batch) if limit.batch else preferred


def acquire_slot(rpm: int | None, *, key: object | None = None) -> float:
    """Block until this bucket's next send slot, then return the seconds slept.

    Difference from ``sleep_between_batches``: there is no "first call is free"
    branch.  A cold bucket hands out exactly **one** immediate slot and paces
    every later caller by ``60/rpm``, so N threads starting together are spread
    over the timeline instead of firing as a burst.  Reservation and decrement
    happen inside ``_slot_lock`` (see ``_reserve_slot``), so concurrent callers
    can never be granted the same slot.

    Pass a namespaced *key* (e.g. ``"taiwan:twse"``) to keep a source's quota
    separate from every other bucket.  Without a key the legacy rpm-value
    bucketing is used, so existing callers are unaffected.
    """
    interval = batch_interval(rpm)
    if interval <= 0:
        return 0.0
    wait = _reserve_slot(rpm or -1, interval, key)
    if wait > 0:
        time.sleep(wait)
    return max(wait, 0.0)
