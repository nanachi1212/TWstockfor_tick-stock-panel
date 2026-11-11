# 資料源插件開發指南

資料源插件是可選的台股行情資料來源(例如 FinMind 等第三方服務),作為獨立模組放在
`backend/app/plugins/` 下。使用者**手動安裝依賴**後才可用(開發模式);不安裝完全不影響主功能。


## 快速上手

一個插件 = 一個目錄 + 一個 `plugin.yaml` 清單:

```
backend/app/plugins/<your_plugin>/
├── plugin.yaml          # 清單(必需)
├── provider.py          # Provider 實作(必需)
├── ...                  # 橋接/依賴文件(按需)
```

### plugin.yaml 欄位

```yaml
name: my_source                          # 唯一標識, 只允許 [a-z0-9_], 也是 provider name
display_name: "我的資料源"                 # 設定頁顯示名
runtime: python                          # 執行階段類型: node | python | none
entry: app.plugins.my_source.provider:MyProvider   # provider 類的匯入路徑
check: app.plugins.my_source.bridge:availability   # 可用性檢測函式(可選)
datasets: [daily, adj_factor, minute, realtime]     # 支援的資料集
api_key_env: MY_SOURCE_API_KEY           # (可選)宣告後設定頁提供 Key 輸入框
hidden: false                            # (可選)true = 已載入但對設定頁隱藏,不註冊不展示
description: "資料源描述"
install_hint: "pip install xxx"          # 未裝依賴時顯示的安裝提示
```

#### api_key_env(介面設定 API Key)

宣告 `api_key_env` 的插件可以在設定頁的資料源卡片中直接填寫 Key, 對齊
既有「先探後存」語意:

1. entry 模組需提供模組級 `probe_api_key(key) -> (ok, reason)`,
   後端用候選 Key 實探一次, **無效不落盤**
2. 有效則寫入 `data/user_data/secrets.json` 的 `{name}_api_key` 欄位
   (0600 權限, 優先順序高於 `.env` / 環境變數)
3. 儲存後自動重載資料源註冊表, 插件即刻變為可切換
4. 插件取 Key 用 `secrets_store.get_env_backed_secret("{name}_api_key", api_key_env)`,
   保證 secrets.json 與 .env 兩條設定路徑一致

### runtime 欄位說明

| runtime | 含義 | 典型場景 |
|---|---|---|
| `python` | 純 Python 依賴, `pip install` | 官方提供 Python 套件的資料服務 |
| `node` | 需要 Node.js 執行階段, `npm install` | 官方只提供 Node.js 套件的資料服務(Docker 映像檔不含 Node.js 執行階段) |
| `none` | 無額外依賴 | 純 HTTP API 源 |

`runtime` 欄位當前僅用於 UI 展示, 實際依賴檢測由 `check` 函式負責。

### check 函式

插件自己負責檢測依賴是否已安裝。後端啟動時會呼叫此函式:

```python
# app/plugins/my_source/bridge.py
def availability() -> tuple[bool, str]:
    """回傳 (是否可用, 原因)。不拋異常。"""
    try:
        import my_source_sdk  # noqa: F401
        return True, "ok"
    except ImportError:
        return False, "未安裝 my_source_sdk, 執行: pip install my_source_sdk"
```

- **可用** → 插件註冊進路由表, 設定頁可切換
- **不可用** → 設定頁顯示插件卡片但灰顯, 展示 `install_hint`

## Provider 介面契約

Provider 是一個普通 Python 類(無需繼承基類), 實作以下方法簽名。方法簽名對齊
`GenericHTTPProvider`, 這樣 services 層(kline_sync / quote_service 等)的路由邏輯
零改動即可路由到插件。

```python
class MyProvider:
    name = "my_source"
    builtin = True  # 標記為內建(不可被使用者編輯/刪除)

    def __init__(self):
        self.config = MyConfig()  # 需有 .datasets 屬性(dict, key 是資料集名)

    def close(self) -> None:
        """清理資源(load_all 重建註冊表時會調)。"""

    def get_daily(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None) -> pl.DataFrame:
        """日K: 回傳 schema [symbol, date, open, high, low, close, volume, amount]"""

    def get_adj_factors(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None) -> pl.DataFrame:
        """除權因子: 回傳 schema [symbol, trade_date, ex_factor]"""

    def get_minute(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None, freq="1m") -> pl.DataFrame:
        """分鐘K: 回傳 schema [symbol, datetime, open, high, low, close, volume, amount]"""

    def get_realtime(self) -> list[dict]:
        """全市場即時快照: 回傳 list[dict], 每行含 symbol/last_price/prev_close/open/high/low/volume"""

    def get_instruments(self, asset_type="stock") -> list[dict]:
        """標的維表(可選): 回傳既有標準化 Instrument 欄位, 供標的維表同步重用 flatten"""
```

### config.datasets 的作用

`provider_has_dataset(name, dataset)` 透過 `dataset in provider.config.datasets` 判斷。
這是 services 層路由的關鍵: 使用者在設定頁選了插件, 但某資料集未聲明時, 該資料集
明確回報該資料集不可用，不回退到其他來源。

```python
class MyConfig:
    datasets = {"daily": ..., "realtime": ...}  # key 是資料集名, value 任意
```

## 現有插件參考

目前儲存庫內沒有內建插件；下列目錄結構與 `plugin.yaml` 規範即為新插件的範本。

## 路由機制(無需關心, 僅參考)

後端啟動時, `loader.py` 的 `_load_builtin_plugins()` 掃描 `plugins/` 目錄:
1. 讀每個子目錄的 `plugin.yaml`
2. 調 `check` 函式檢測可用性
3. 可用 → 動態 import `entry` 指向的 Provider 類 → 註冊進 `_PROVIDERS`
4. 不可用 → 記錄狀態, 設定頁顯示但不可切換

註冊後, 插件和使用者 YAML 自訂源走**完全相同的路由路徑**(services 層的
`provider_has_dataset` / `get_provider` 呼叫), 無需額外整合程式碼。
