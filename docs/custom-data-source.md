# 自訂資料源接入

本專案預設使用內建資料源。自訂資料源是一個可選擴充: 外部 HTTP 服務負責取數和整理, 本專案只把回傳結果映射成內部標準欄位, 然後重用現有儲存、指標、enriched、策略和前端展示邏輯。

內建行情僅支援台股。自訂來源未提供選定資料集或取數失敗時，明確回報不可用，不切換到其他來源。

## 支援範圍

當前自訂源支援五類資料:

| 資料集 | 設定名 | 說明 |
| --- | --- | --- |
| 日K | `daily` | 批次回傳一組股票在指定區間內的日K |
| 除權因子 | `adj_factor` | 批次回傳一組股票的復權因子 |
| 即時行情 | `realtime` | 回傳全市場快照,用於盤中 enriched 增量計算 |
| 分鐘K | `minute` | 回傳 1m 分鐘K(需映射出 symbol / datetime / OHLC / 量額) |
| 財務資料 | `financial` | 一個設定覆蓋全部財務表,請求時把表名作為參數傳給上游;欄位由資料源決定,僅需映射出 symbol |

深度盤口(depth5)目前沒有資料集契約,仍由內建資料源提供。

## 設定位置

把 YAML 放到執行資料目錄下:

```text
data/data_sources/*.yaml
```

Dev 模式下，預設位置是專案根目錄的 `data/`；Docker 部署中，專案的 `data/` 會掛載為容器內的 `/app/data`。可透過 `DATA_DIR` 覆蓋。

修改 YAML 後可在「設定 -> 資料源」按一下「重新載入」,或呼叫:

```bash
curl -X POST http://127.0.0.1:3018/api/settings/data-sources/reload
```

## 最小 YAML

```yaml
name: mock_source
display_name: "Mock 自訂資料源"
auth:
  type: none

datasets:
  daily:
    url: http://127.0.0.1:3021/daily
    method: POST
    batch: 100
    rpm: 200
    response_path: data
    field_map:
      stock_id: symbol
      trade_date: date
      open: open
      high: high
      low: low
      close: close
      vol: volume
      amt: amount
    transforms:
      date: "parse_date(value, '%Y-%m-%d')"

  adj_factor:
    url: http://127.0.0.1:3021/adj_factor
    method: POST
    batch: 100
    rpm: 200
    response_path: data
    field_map:
      stock_id: symbol
      trade_date: trade_date
      factor: ex_factor
    transforms:
      trade_date: "parse_date(value, '%Y-%m-%d')"

  realtime:
    url: http://127.0.0.1:3021/realtime
    method: GET
    rpm: 60
    response_path: data
    field_map:
      stock_id: symbol
      name: name
      last: last_price
      pre_close: prev_close
      open: open
      high: high
      low: low
      vol: volume
      amt: amount
      pct: change_pct
      amount_change: change_amount
      amplitude: amplitude
      turnover: turnover_rate
```

## 欄位契約

### daily 必填

| 內部欄位 | 含義 |
| --- | --- |
| `symbol` | 標準代號,如 `2330.TWSE` |
| `date` | 交易日 |
| `open` / `high` / `low` / `close` | 不復權 OHLC |
| `volume` | 成交量 |
| `amount` | 成交額 |

### adj_factor 必填

| 內部欄位 | 含義 |
| --- | --- |
| `symbol` | 標準代號 |
| `trade_date` | 除權日期 |
| `ex_factor` | 復權因子 |

### realtime 必填

| 內部欄位 | 含義 |
| --- | --- |
| `symbol` | 標準代號 |
| `last_price` | 最新價 |
| `prev_close` | 昨收 |
| `open` / `high` / `low` | 當日 OHLC |
| `volume` | 成交量 |

建議即時介面額外提供 `amount`、`change_pct`、`change_amount`、`amplitude`、`turnover_rate`、`name`。缺失時部分欄位會由 pipeline 回算,但精度取決於可用輸入。

`change_pct` 和 `amplitude` 使用小數制,例如 `0.0366` 表示 `3.66%`(`turnover_rate` 同)。若介面直接回傳百分數值 `3.66`,即時行情會按截面中位數自動歸一為小數制,但仍建議介面直接提供小數制以避免小樣本歧義。

## 請求約定

- `daily` / `adj_factor` 會按 `batch` 切分 symbols。
- POST 請求會發送 JSON body: `symbols`、`start_time`、`end_time`。
- GET 請求會發送 query 參數: `symbols=2330.TWSE,6488.TPEX`。
- `realtime` 必須是全市場快照介面,不支援逐個 symbol 拉即時行情。

可透過這些欄位改參數名:

```yaml
symbols_param: symbols
start_param: start_time
end_param: end_time
```

分鐘資料源如果需要區分資產類型或週期，可繼續設定：

```yaml
asset_type_param: asset_type
freq_param: period
```

設定後，分鐘請求會分別傳入 `stock` / `etf` / `index` 和 `1m`；留空時不向上游發送這兩個參數，以相容已有資料源。

### 請求超時

每個資料集可單獨設定請求超時（秒），預設 30：

```yaml
timeout: 60
```

留空或省略時用預設 30 秒，可設定範圍為大於 0 且不超過 300 秒；該值對資料同步與「試拉測試」均生效。在設定頁編輯資料源時可在「超時」輸入框修改（與 批次 / RPM / 響應路徑 同行）。「試拉測試」直接使用當前表單內容，新增資料源或尚未儲存的修改也可測試。

## 鑑權

支援三種簡單鑑權:

```yaml
auth:
  type: bearer
  token_env: MY_DATA_TOKEN
```

```yaml
auth:
  type: header
  header: X-Token
  token_env: MY_DATA_TOKEN
```

```yaml
auth:
  type: query
  param: token
  token_env: MY_DATA_TOKEN
```

Token 可以放在系統環境變數或專案 `.env` 中。

## 聯調流程

1. 啟動 mock 資料源:

```bash
cd docs/examples/custom-data-source
python mock_server.py
```

2. 複製範例設定:

```bash
mkdir -p data/data_sources
cp docs/examples/custom-data-source/mock_source.yaml data/data_sources/mock_source.yaml
```

3. 在「設定 -> 資料源」按一下「重新載入」。

4. 使用「試拉測試」選擇 `mock_source` 和 `daily` / `adj_factor` / `realtime`。

5. 儲存資料源選擇:

- 日K: `mock_source`
- 除權因子: `same_as_daily` 或 `mock_source`
- 即時行情: `mock_source`

6. 觸發同步或開啟即時行情。

## 常見錯誤

| 現象 | 處理 |
| --- | --- |
| 清單裡沒有 custom 源 | 檢查 YAML 是否放在 `data/data_sources/` 並按一下重新載入 |
| errors 提示 missing mapped fields | `field_map` 沒映射到必填內部欄位 |
| 試拉 rows 為 0 | 檢查 `response_path` 是否指向數組 |
| 日期列全為空 | 檢查 `parse_date` 的格式是否和回傳值一致 |
| 即時行情沒重新整理 | 確認即時資料源已儲存為 custom,且回傳全市場快照 |

## 用 AI 生成映射設定

如果你的資料源 API 文件比較複雜,可以把 API 文件和回傳範例丟給 AI,讓它幫你生成 `field_map` 和 YAML 設定。

### 操作步驟

1. 從你的資料源取得 API 文件(介面位址、請求方式、回傳欄位說明)
2. 試拉一次,拿到回傳的 JSON 範例
3. 把下面的 prompt 範本 + API 文件 + JSON 範例一起發給 AI
4. 把 AI 生成的 YAML 貼到 `data/data_sources/xxx.yaml`
5. 在設定頁點「重新載入」,再「試拉測試」驗證

### Prompt 範本

複製以下內容發給 AI(替換方括號部分):

```text
我在設定一個自訂資料源接入股票面板。請根據我的 API 文件和回傳範例,生成 YAML 設定。

要求:
1. 輸出標準 YAML 設定,包含 name / display_name / auth / datasets
2. 每個資料集的 field_map 把我的介面欄位名映射到內部欄位名
3. 日期類欄位如果格式不是 YYYY-MM-DD, 加上 transforms 裡的 parse_date
4. 只設定我能提供的介面, 不存在的資料集不要寫

內部欄位對照表:

日K (daily):
  symbol = 股票代號, 格式 2330.TWSE / 6488.TPEX
  date = 交易日期
  open / high / low / close = OHLC
  volume = 成交量
  amount = 成交額

除權因子 (adj_factor):
  symbol = 股票代號
  trade_date = 除權日期
  ex_factor = 復權因子

即時行情 (realtime):
  symbol = 股票代號
  last_price = 最新價
  prev_close = 昨收價
  open / high / low = 當日 OHLC
  volume = 成交量
  amount = 成交額
  change_pct = 漲跌幅 (小數, 0.0366 = 3.66%)
  change_amount = 漲跌額
  amplitude = 振幅
  turnover_rate = 換手率 (小數, 0.05 = 5%; 若上游回傳 5 表示 5%, 設定 transforms: turnover_rate: "value / 100")

分鐘K (minute):
  symbol = 股票代號
  datetime = 時間戳 (YYYY-MM-DD HH:MM:SS)
  open / high / low / close = OHLC
  volume = 成交量
  amount = 成交額

=== 我的 API 文件 ===
[把你的介面文件貼這裡: URL / 請求方式 / 參數 / 回傳欄位說明]

=== 回傳 JSON 範例 ===
[把試拉的 JSON 回傳貼這裡]
```

AI 會輸出類似這樣的結果:

```yaml
name: my_source
display_name: "我的資料源"
auth:
  type: bearer
  token_env: MY_API_TOKEN

datasets:
  daily:
    url: https://api.example.com/kline
    method: POST
    batch: 100
    rpm: 200
    response_path: data.list
    field_map:
      stock_id: symbol
      trade_date: date
      open: open
      vol: volume
    transforms:
      date: "parse_date(value, '%Y%m%d')"
```

把這段 YAML 儲存為 `data/data_sources/my_source.yaml`,然後在設定頁重新載入即可。
