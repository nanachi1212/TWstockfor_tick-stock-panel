"""DuckDB 標識符安全原語 (Issue #150: ext_columns SQL 注入防護)。

集中提供標識符轉義與校驗, 供所有拼接 DuckDB SQL 的 sink 複用, 消除
"screener 有防護、kline/watchlist 沒有"的不一致根因。
"""
from __future__ import annotations

import re

# 合法標識符白名單: 字母 / 數字 / 下劃線。與 CreateExtReq.id 創建端校驗一致,
# 用於 config_id 等「純使用者可控 + 僅合法標識符」字段的深度防禦。
EXT_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def quote_ident(name: str) -> str:
    """把列名/字段名轉義為 DuckDB 雙引號標識符。

    對任意字元安全: 僅對內嵌的雙引號做雙寫轉義 (``"`` → ``""``), 再用雙引號包裹。
    轉義後整個串成為單個「帶引號標識符」, 即便 name 含 ``--``/``;``/``UNION``/``COPY``
    也只會被 DuckDB 當作字面列名, 不會被解釋為 SQL 語法, 從根上杜絕注入。

    用於 field_name: 因 FieldDef.name 可合法含中文/點/特殊字元, 不能用白名單,
    只能在 sink 處轉義。
    """
    return '"' + name.replace('"', '""') + '"'


def is_valid_ext_ident(name: str) -> bool:
    """config_id 等純標識符字段是否僅含合法字元 (字母/數字/下劃線)。"""
    return bool(EXT_IDENT_RE.match(name))
