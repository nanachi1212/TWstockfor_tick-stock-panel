"""ext_columns SQL 注入防護測試 (Issue #150)。

覆蓋:
- quote_ident 對各注入 payload 的轉義正確性 (含 RCE 向量 COPY TO)
- 端到端: 用真實 DuckDB 驗證惡意 field_name 不會產生檔案 / 不洩漏資料
- is_valid_ext_ident 白名單 (config_id 防禦)
- 迴歸: 合法特殊字元欄位名 (中文/點) 仍能透傳到 sink (證明未誤傷資料)
"""
from __future__ import annotations

import os
import tempfile

import duckdb
import pytest

from app.db_safe import is_valid_ext_ident, quote_ident


# ===== quote_ident 轉義正確性 =====

def test_quote_ident_wraps_in_double_quotes():
    assert quote_ident("close") == '"close"'


def test_quote_ident_escapes_embedded_double_quote():
    # 雙引號雙寫: 欄位名含 " 時必須轉義, 否則可逃逸識別字
    assert quote_ident('a"b') == '"a""b"'


@pytest.mark.parametrize("payload", [
    'x" UNION SELECT 1',
    'x"); DROP TABLE t; --',
    'x" -- comment',
    'x"; --',
    'x" COPY (SELECT 1) TO \'/tmp/evil\' --',
])
def test_quote_ident_neutralizes_injection_payloads(payload):
    """轉義後的串必須是一個整體帶引號識別字, 內部雙引號被雙寫。"""
    escaped = quote_ident(payload)
    # 整體仍是 "...." 包裹, 內部每個原始 " 都變成 ""
    assert escaped.startswith('"') and escaped.endswith('"')
    # 原始 payload 裡的每個 " 都被雙寫
    inner = escaped[1:-1]
    assert inner == payload.replace('"', '""')


# ===== 端到端注入測試 (真實 DuckDB) =====

def _build_ext_view(con: duckdb.DuckDBPyConnection, field_name: str) -> None:
    """建一個含指定 field_name 列的 ext_x 檢視表, 模擬項目擴展資料。"""
    # 欄名含特殊字元時, 建表也必須用雙引號轉義
    con.execute(f'CREATE OR REPLACE TABLE _t_{id(field_name) % 10000} (symbol VARCHAR, {quote_ident(field_name)} DOUBLE)')
    con.execute(f"INSERT INTO _t_{id(field_name) % 10000} VALUES ('000001.SZ', 12.3)")
    con.execute(f'CREATE OR REPLACE VIEW ext_x AS SELECT symbol, {quote_ident(field_name)} FROM _t_{id(field_name) % 10000}')


def test_sink_with_malicious_field_name_does_not_write_file():
    """惡意 field_name 經 quote_ident 後, COPY TO payload 不產生檔案。

    復現 Issue #150 的 RCE 向量: 若 field_name 裸拼, 攻擊者可構造 COPY TO 寫任意檔案。
    轉義後, 整個 payload 變成字面欄名, DuckDB 查詢報「列不存在」, 不會執行 COPY。
    """
    tmp = os.path.join(tempfile.gettempdir(), "tf_sec_inject_sink.out")
    if os.path.exists(tmp):
        os.remove(tmp)

    con = duckdb.connect(":memory:")
    _build_ext_view(con, "close")  # 合法欄名

    # 攻擊 payload: 企圖在查詢裡塞 COPY TO 寫檔案
    evil = 'close" UNION SELECT * FROM (COPY (SELECT 1) TO \'' + tmp + '\') --'
    sql = f'SELECT symbol, {quote_ident(evil)} FROM ext_x'
    # 轉義後查詢合法列失敗 → 報 Binder Error, 絕不會執行 COPY
    with pytest.raises(Exception):
        con.execute(sql).fetchall()

    # 關鍵斷言: 檔案未被建立 (COPY 未執行)
    assert not os.path.exists(tmp), "RCE: COPY TO 寫檔案成功, 注入未堵死!"


def test_sink_with_malicious_field_name_does_not_leak_data():
    """惡意 field_name 企圖用 UNION 讀其它表資料 → 轉義後僅作字面欄名, 查詢失敗。"""
    con = duckdb.connect(":memory:")
    _build_ext_view(con, "close")
    con.execute("CREATE TABLE secret (pw VARCHAR)")
    con.execute("INSERT INTO secret VALUES ('leaked')")

    evil = 'close" UNION SELECT pw FROM secret --'
    sql = f'SELECT symbol, {quote_ident(evil)} FROM ext_x'
    # 不會回傳 secret 表內容, 而是出錯 (列 close"... UNION... 不存在)
    with pytest.raises(Exception):
        con.execute(sql).fetchall()


# ===== is_valid_ext_ident 白名單 =====

@pytest.mark.parametrize("ident, expected", [
    ("abc", True),
    ("abc_123", True),
    ("ABC_xyz", True),
    ("x_y", True),
    ("", False),
    ("a b", False),          # 含空格
    ("a-b", False),          # 含連字元
    ("a.b", False),          # 含點
    ("a\"b", False),         # 含雙引號
    ("a'b", False),          # 含單引號
    ("a;b", False),          # 含分號
    ("中文", False),         # 含中文 (config_id 不允許, 但 field_name 允許 → 見迴歸測試)
])
def test_is_valid_ext_ident(ident, expected):
    assert is_valid_ext_ident(ident) is expected


# ===== parser 白名單: 拒絕惡意 config_id =====

class _NoLegacySQL:
    def __getattr__(self, name):
        raise AssertionError(f"Retired extension path accessed repository: {name}")


def test_watchlist_retired_ext_parameter_never_runs_sql(monkeypatch):
    from types import SimpleNamespace

    from app.api import watchlist

    monkeypatch.setattr(watchlist.watchlist, "list_symbols", lambda: [])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=_NoLegacySQL())))
    result = watchlist.watchlist_enriched(request, ext_columns="evil'; DROP--.field")
    assert result["rows"] == []


def test_kline_retired_ext_parameter_never_runs_sql():
    from app.api.kline import _attach_ext

    response = {"symbol": "2330.TWSE", "rows": [{"close": 100.0}]}
    result = _attach_ext(response, _NoLegacySQL(), "2330.TWSE", "evil'; DROP--.field")
    assert result is response
    assert result["rows"] == [{"close": 100.0}]


# ===== 迴歸: 合法特殊字元欄位名未被誤傷 =====

def test_legal_special_field_name_still_queryable():
    """field_name 可合法含中文/點 (FieldDef.name 無校驗), quote_ident 必須支援。"""
    con = duckdb.connect(":memory:")
    for name in ["漲幅", "本益比.靜態", "field.name"]:
        _build_ext_view(con, name)
        # 用 quote_ident 轉義後, 特殊欄名仍能正確查詢
        sql = f'SELECT symbol, {quote_ident(name)} FROM ext_x'
        rows = con.execute(sql).fetchall()
        assert rows and rows[0][0] == "000001.SZ", f"合法欄位名 {name} 查詢失敗 (被誤傷)"
    con.close()
