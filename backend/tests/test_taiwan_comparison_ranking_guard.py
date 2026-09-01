"""Tests for the deterministic ranking/recommendation-language runtime guard (Phase 7G).

`detect_ranking_language()` is a focused, curated bilingual phrase blocklist —
NOT a general NLP/sentiment validator — applied only to AI-generated
comparison prose fields. These tests prove:
  - legitimate objective comparison language survives (returns None)
  - Traditional Chinese investment-ranking/recommendation phrasing is rejected
  - English investment-ranking/recommendation phrasing is rejected
"""
from app.taiwan.comparison_ai_research import detect_ranking_language


# ── Legitimate objective comparison language MUST survive ─────


OBJECTIVE_PHRASES = [
    "2330.TWSE 報酬率較高",
    "RSI 較高",
    "相對強度較強",
    "higher 5D return",
    "A 的成交量能高於 B",
    "外資買超金額較大",
    "foreign net buying was larger for symbol A",
    "2330.TWSE 5 日報酬優於 2881.TWSE",  # bare "優於" (numeric comparison) is objective
    "站上均線的天數較多",
    "距離 20 日高點較近",
]


def test_objective_comparison_language_survives():
    for phrase in OBJECTIVE_PHRASES:
        assert detect_ranking_language(phrase) is None, f"false positive on objective phrase: {phrase!r}"


def test_empty_and_none_text_survives():
    assert detect_ranking_language("") is None
    assert detect_ranking_language(None) is None


# ── Traditional Chinese ranking/recommendation wording MUST be rejected ──


ZH_RANKING_PHRASES = [
    "2330.TWSE 是最佳選擇",
    "2881.TWSE 最值得投資",
    "0050.TWSE 最具投資價值",
    "2330.TWSE 更具投資吸引力",
    "8069.TPEX 是較佳投資機會",
    "2330.TWSE 為首選標的",
    "2330.TWSE 勝出",
    "2330.TWSE 優於 2881.TWSE，建議優先投資",  # "優於" + preference verb -> flagged
]


def test_traditional_chinese_ranking_language_rejected():
    for phrase in ZH_RANKING_PHRASES:
        assert detect_ranking_language(phrase) is not None, f"missed ranking phrase: {phrase!r}"


# ── English ranking/recommendation wording MUST be rejected ───


EN_RANKING_PHRASES = [
    "2330.TWSE is the best pick",
    "2881.TWSE is a top pick",
    "0050.TWSE is a better investment opportunity",
    "2330.TWSE is a more attractive investment",
    "2330.TWSE is more attractive than 2881.TWSE",
    "2330.TWSE is the winner",
    "2330.TWSE should outperform 2881.TWSE",
    "2330.TWSE has the highest investment value",
]


def test_english_ranking_language_rejected():
    for phrase in EN_RANKING_PHRASES:
        assert detect_ranking_language(phrase) is not None, f"missed ranking phrase: {phrase!r}"
