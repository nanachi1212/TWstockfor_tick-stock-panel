import polars as pl
import pytest
from fastapi import HTTPException

from app.api.ext_data import _display_ext_label, _filter_dimension_member_rows


def test_filter_dimension_member_rows_matches_complete_tags() -> None:
    rows = pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
        "所属概念": ["人工智能;芯片", "人工智能体;机器人", "芯片 / 人工智能", None],
    })

    result = _filter_dimension_member_rows(rows, "所属概念", "人工智能")

    assert result.get_column("symbol").to_list() == ["000001.SZ", "000003.SZ"]


def test_filter_dimension_member_rows_matches_industry_hierarchy() -> None:
    rows = pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ"],
        "所属行业": ["金融-银行-股份制银行", "电子-半导体-数字芯片", "电子元件"],
    })

    result = _filter_dimension_member_rows(rows, "所属行业", "电子")

    assert result.get_column("symbol").to_list() == ["000002.SZ"]


def test_filter_dimension_member_rows_rejects_unknown_field() -> None:
    rows = pl.DataFrame({"symbol": ["000001.SZ"]})

    with pytest.raises(HTTPException, match="字段 '所属行业' 不存在"):
        _filter_dimension_member_rows(rows, "所属行业", "银行")


# TAIWAN_LOCALIZATION_POLISH follow-up — 已存在的 data/ext_data/*/config.json 可能
# 仍留有旧版简体 label ("扩展概念"/"扩展行业"); display 层需正规化为正体中文,
# 但不得连带影响未知的其他 label 值 (不是通用简繁转换)。
def test_display_ext_label_normalizes_known_legacy_labels() -> None:
    assert _display_ext_label("扩展概念") == "擴展概念"
    assert _display_ext_label("扩展行业") == "擴展行業"


def test_display_ext_label_passes_through_unknown_labels_unchanged() -> None:
    # 新建 config (ext_presets.py 已是正体默认) 与使用者自订 label 都应原样返回。
    assert _display_ext_label("擴展概念") == "擴展概念"
    assert _display_ext_label("我的自訂表") == "我的自訂表"
    assert _display_ext_label("") == ""
