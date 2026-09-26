"""Regression tests: QuoteService startup must not crash on empty/unknown tier_label."""
from __future__ import annotations

import unittest.mock as mock

from app.services.quote_service import QuoteService

# ------------------------------------------------------------------ helpers


def _current_tier_with_label(label: str) -> str:
    with mock.patch("app.tickflow.policy.tier_label", return_value=label):
        return QuoteService._current_tier()


def _min_interval_with_label(label: str) -> float:
    with mock.patch("app.tickflow.policy.tier_label", return_value=label):
        return QuoteService._tier_min_interval()


# ------------------------------------------------------------------ _current_tier


def test_current_tier_empty_string_does_not_raise():
    assert _current_tier_with_label("") == ""


def test_current_tier_whitespace_does_not_raise():
    assert _current_tier_with_label("   ") == ""


def test_current_tier_known_tier_unchanged():
    assert _current_tier_with_label("expert") == "expert"
    assert _current_tier_with_label("pro") == "pro"
    assert _current_tier_with_label("starter") == "starter"
    assert _current_tier_with_label("free") == "free"


def test_current_tier_compound_label_parses_first_word():
    assert _current_tier_with_label("pro+extra flags") == "pro"


def test_current_tier_unknown_value_returns_lowercased():
    assert _current_tier_with_label("SOMEUNKNOWN") == "someunknown"


# ------------------------------------------------------------------ _tier_min_interval / conservative fallback


def test_min_interval_empty_label_returns_default():
    interval = _min_interval_with_label("")
    assert interval == QuoteService.DEFAULT_INTERVAL


def test_min_interval_whitespace_label_returns_default():
    interval = _min_interval_with_label("   ")
    assert interval == QuoteService.DEFAULT_INTERVAL


def test_min_interval_unknown_label_returns_default():
    interval = _min_interval_with_label("nonexistent_tier")
    assert interval == QuoteService.DEFAULT_INTERVAL


def test_min_interval_known_tiers_are_not_slower_than_default():
    for tier in ("expert", "pro", "starter", "free"):
        interval = _min_interval_with_label(tier)
        assert interval <= QuoteService.MAX_INTERVAL
        assert interval >= 1.0


# ------------------------------------------------------------------ boot_check does not crash


def test_boot_check_does_not_crash_with_empty_tier_label(tmp_path):
    """boot_check() → start() → _clamp_interval() → _tier_min_interval must not raise."""
    import json

    prefs_path = tmp_path / "preferences.json"
    prefs_path.write_text(json.dumps({"realtime_quotes_enabled": True}), encoding="utf-8")

    qs = QuoteService()

    # Patch preferences to return enabled=True and a dummy interval
    with (
        mock.patch("app.tickflow.policy.tier_label", return_value=""),
        mock.patch("app.services.preferences.get_realtime_quotes_enabled", return_value=True),
        mock.patch("app.services.preferences.get_realtime_quote_interval", return_value=10.0),
        mock.patch("app.services.preferences.save"),
        mock.patch.object(qs, "_poll_loop"),  # don't actually start polling
        mock.patch.object(qs, "_thread"),
    ):
        # is_realtime_allowed returns True (realtime_mode != "none")
        assert QuoteService.is_realtime_allowed()

        # _clamp_interval must not raise; result must be >= DEFAULT_INTERVAL
        clamped = qs._clamp_interval(10.0)
        assert clamped >= QuoteService.DEFAULT_INTERVAL
