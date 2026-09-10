# 數據源插件開發指南

數據源插件是可選的行情數據來源(stock-sdk、akshare 等),作為獨立模塊放在
`backend/app/plugins/` 下。用戶**手動安裝依賴**後才可用(開發模式);不安裝完全不影響主功能。

> ⚠️ **Docker 默認不打包 stock-sdk**(合規考慮:它抓取第三方財經網站接口,存在版權與反爬風險)。如需在 Docker 中啟用,構建時傳 `--build-arg INCLUDE_STOCKSDK=1`,使用風險自負。下方"手動安裝依賴"適用於開發模式及自定義 Docker 構建。

## 快速上手

一個插件 = 一個目錄 + 一個 `plugin.yaml` 清單:

```
backend/app/plugins/<your_plugin>/
├── plugin.yaml          # 清單(必需)
├── provider.py          # Provider 實現(必需)
├── ...                  # 橋接/依賴文件(按需)
```

### plugin.yaml 字段

```yaml
name: my_source                          # 唯一標識, 只允許 [a-z0-9_], 也是 provider name
display_name: "我的數據源"                 # 設置頁顯示名
runtime: python                          # 運行時類型: node | python | none
entry: app.plugins.my_source.provider:MyProvider   # provider 類的導入路徑
check: app.plugins.my_source.bridge:availability   # 可用性檢測函數(可選)
datasets: [daily, adj_factor, minute, realtime]     # 支持的數據集
api_key_env: MY_SOURCE_API_KEY           # (可選)聲明後設置頁提供 Key 輸入框
hidden: false                            # (可選)true = 已加載但對設置頁隱藏,不註冊不展示
description: "數據源描述"
install_hint: "pip install xxx"          # 未裝依賴時顯示的安裝提示
```

#### api_key_env(界面配置 API Key)

聲明 `api_key_env` 的插件可以在設置頁的數據源卡片中直接填寫 Key, 對齊
TickFlow 的「先探後存」語義:

1. entry 模塊需提供模塊級 `probe_api_key(key) -> (ok, reason)`,
   後端用候選 Key 實探一次, **無效不落盤**
2. 有效則寫入 `data/user_data/secrets.json` 的 `{name}_api_key` 字段
   (0600 權限, 優先級高於 `.env` / 環境變量)
3. 保存後自動重載數據源註冊表, 插件即刻變為可切換
4. 插件取 Key 用 `secrets_store.get_env_backed_secret("{name}_api_key", api_key_env)`,
   保證 secrets.json 與 .env 兩條配置路徑一致

### runtime 字段說明

| runtime | 含義 | 典型場景 |
|---|---|---|
| `python` | 純 Python 依賴, `pip install` | akshare、tushare |
| `node` | 需要 Node.js 運行時, `npm install` | stock-sdk(Docker 默認不打包,見 [deployment.md](./deployment.md)) |

> stock-sdk 在 Docker 中默認不打包(合規考慮);如需啟用,構建時傳 `--build-arg INCLUDE_STOCKSDK=1`,開發模式下需手動 `npm install`。
| `none` | 無額外依賴 | 純 HTTP API 源 |

`runtime` 字段當前僅用於 UI 展示, 實際依賴檢測由 `check` 函數負責。

### check 函數

插件自己負責檢測依賴是否已安裝。後端啟動時會調用此函數:

```python
# app/plugins/my_source/bridge.py
def availability() -> tuple[bool, str]:
    """返回 (是否可用, 原因)。不拋異常。"""
    try:
        import akshare  # noqa: F401
        return True, "ok"
    except ImportError:
        return False, "未安裝 akshare, 運行: pip install akshare"
```

- **可用** → 插件註冊進路由表, 設置頁可切換
- **不可用** → 設置頁顯示插件卡片但灰顯, 展示 `install_hint`

## Provider 接口契約

Provider 是一個普通 Python 類(無需繼承基類), 實現以下方法簽名。方法簽名對齊
`GenericHTTPProvider`, 這樣 services 層(kline_sync / quote_service 等)的路由邏輯
零改動即可路由到插件。

```python
class MyProvider:
    name = "my_source"
    builtin = True  # 標記為內置(不可被用戶編輯/刪除)

    def __init__(self):
        self.config = MyConfig()  # 需有 .datasets 屬性(dict, key 是數據集名)

    def close(self) -> None:
        """清理資源(load_all 重建註冊表時會調)。"""

    def get_daily(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None) -> pl.DataFrame:
        """日K: 返回 schema [symbol, date, open, high, low, close, volume, amount]"""

    def get_adj_factors(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None) -> pl.DataFrame:
        """除權因子: 返回 schema [symbol, trade_date, ex_factor]"""

    def get_minute(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None, freq="1m") -> pl.DataFrame:
        """分鐘K: 返回 schema [symbol, datetime, open, high, low, close, volume, amount]"""

    def get_realtime(self) -> list[dict]:
        """全市場實時快照: 返回 list[dict], 每行含 symbol/last_price/prev_close/open/high/low/volume"""

    def get_instruments(self, asset_type="stock") -> list[dict]:
        """標的維表(可選): 返回 tickflow Instrument 形狀的行, 供 instrument_sync 複用 flatten"""
```

### config.datasets 的作用

`provider_has_dataset(name, dataset)` 通過 `dataset in provider.config.datasets` 判斷。
這是 services 層路由的關鍵: 用戶在設置頁選了插件, 但某數據集未聲明時, 該數據集
自動回退 TickFlow。

```python
class MyConfig:
    datasets = {"daily": ..., "realtime": ...}  # key 是數據集名, value 任意
```

## 現有插件參考

- **`backend/app/plugins/fuyao/`** — 同花順官方 REST 數據源(runtime: none, 純 HTTP 零依賴)
  - 當前提供 `realtime`(A 股全市場快照, 分頁拉取); Key 在設置頁卡片直接配置(先探後存), 或 `.env` 配 `FUYAO_API_KEY`
  - `client.py` — httpx 客戶端(X-api-key 認證 + 統一信封解包 + 分頁)
  - `provider.py` — Provider 實現(字段映射、百分數→小數制單位轉換、軟失敗、Key 探測)
  - 單位口徑注意: 扶搖 `price_change_ratio_pct` 為百分數數值(1.74 = +1.74%),
    內部 `change_pct` 契約為小數制, provider 內顯式 / 100(見 CONTRIBUTING §3.1)
- **`backend/app/plugins/stocksdk/`** — Node 型插件, 通過 subprocess 橋接調用 stock-sdk
  - `bridge.py` — Python↔Node 橋接 + availability 檢測
  - `bridge.mjs` — Node 端(併發池、重試、SDK 解析)
  - `provider.py` — Provider 實現(歸一化、分批、錯誤降級)

## 路由機制(無需關心, 僅參考)

後端啟動時, `loader.py` 的 `_load_builtin_plugins()` 掃描 `plugins/` 目錄:
1. 讀每個子目錄的 `plugin.yaml`
2. 調 `check` 函數檢測可用性
3. 可用 → 動態 import `entry` 指向的 Provider 類 → 註冊進 `_PROVIDERS`
4. 不可用 → 記錄狀態, 設置頁顯示但不可切換

註冊後, 插件和用戶 YAML 自定義源走**完全相同的路由路徑**(services 層的
`provider_has_dataset` / `get_provider` 調用), 無需額外集成代碼。
