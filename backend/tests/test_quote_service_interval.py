"""Taiwan polling is independent of removed vendor subscription tiers."""
from unittest.mock import patch

import pytest

from app.services.quote_service import QuoteService


@pytest.mark.parametrize(("requested", "expected"), [(1, 6), (6, 6), (10, 10), (70, 60)])
def test_poll_interval_keeps_supported_bounds(requested, expected):
    assert QuoteService()._clamp_interval(requested) == expected


def test_boot_uses_taiwan_interval_without_subscription_lookup():
    service = QuoteService()
    with (
        patch("app.services.preferences.get_realtime_quotes_enabled", return_value=True),
        patch("app.services.preferences.get_realtime_quote_interval", return_value=10.0),
        patch.object(service, "start") as start,
    ):
        service.boot_check()
    start.assert_called_once()
