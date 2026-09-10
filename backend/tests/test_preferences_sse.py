from app.services import preferences


def test_sse_refresh_pages_drops_removed_page_keys(monkeypatch):
    monkeypatch.setattr(
        preferences,
        "load",
        lambda: {
            "sse_refresh_pages": {
                "overview-market": False,
                "watchlist": False,
                "limit-ladder": True,
            },
        },
    )

    assert preferences.get_sse_refresh_pages() == {
        "overview-market": False,
        "watchlist": False,
    }
