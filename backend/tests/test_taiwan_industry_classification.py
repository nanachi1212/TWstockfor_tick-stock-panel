"""Unit tests for the canonical Taiwan industry code -> readable name resolver.

Phase 8C-C Final Completion — bounded backend correction.
Covers:
  - Known numeric industry codes (TWSE + TPEx official schemes) resolve to
    their verified readable Chinese name.
  - Already-readable names pass through unchanged (idempotent).
  - Unrecognized numeric codes get an honest fallback, never a guessed name.
  - None/empty stays None (ETFs and other uncategorized instruments).
"""
from __future__ import annotations

from app.taiwan.universe.industry_classification import (
    TAIWAN_INDUSTRY_CODE_TO_NAME,
    resolve_industry_name,
)


class TestResolveIndustryName:
    def test_known_tpex_numeric_code_resolves_to_readable_name(self):
        # Code "24" is used by both TWSE and TPEx semiconductor companies.
        assert resolve_industry_name("24") == "半導體業"

    def test_known_twse_numeric_code_resolves_to_readable_name(self):
        assert resolve_industry_name("01") == "水泥工業"

    def test_already_readable_name_is_unchanged(self):
        # Idempotent: a row that was already correctly ingested as a readable
        # name (e.g. via the ISIN-HTML path) must not be altered.
        assert resolve_industry_name("半導體業") == "半導體業"

    def test_unknown_numeric_code_gets_honest_fallback_not_a_guess(self):
        # "91" is a real official code (TDR / depositary-receipt marker, not
        # an industry) that this table deliberately does not map — must not
        # be silently assigned some other real industry's name.
        result = resolve_industry_name("91")
        assert result == "未知產業（91）"
        assert result not in TAIWAN_INDUSTRY_CODE_TO_NAME.values()

    def test_arbitrary_unmapped_numeric_code_gets_honest_fallback(self):
        assert resolve_industry_name("77") == "未知產業（77）"

    def test_none_stays_none(self):
        assert resolve_industry_name(None) is None

    def test_empty_string_becomes_none(self):
        assert resolve_industry_name("") is None
        assert resolve_industry_name("   ") is None

    def test_no_code_and_readable_name_collide_to_different_groups(self):
        # Grouping safety: a raw code and its own readable name must resolve
        # to the exact same canonical string, so a mixed legacy/new dataset
        # groups together instead of splitting into "24" and "半導體業".
        assert resolve_industry_name("24") == resolve_industry_name("半導體業")

    def test_mapping_table_has_no_duplicate_names_across_codes(self):
        # Each of the 34 verified codes must map to a distinct industry —
        # a duplicate would indicate a bad correlation during derivation.
        names = list(TAIWAN_INDUSTRY_CODE_TO_NAME.values())
        assert len(names) == len(set(names))

    def test_code_91_is_deliberately_excluded_from_the_verified_table(self):
        assert "91" not in TAIWAN_INDUSTRY_CODE_TO_NAME
