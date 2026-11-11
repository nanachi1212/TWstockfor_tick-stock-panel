# 自訂資料源 mock 聯調範例

這個目錄提供一個本地 mock HTTP 資料源,用於驗證本專案的自訂資料源接入鏈路。

## 執行 mock 服務

```bash
cd docs/examples/custom-data-source
python mock_server.py
```

服務預設監聽:

```text
http://127.0.0.1:3021
```

端點:

| 端點 | 資料 |
| --- | --- |
| `/daily` | 日K |
| `/adj_factor` | 除權因子 |
| `/realtime` | 全市場即時快照 |

## 接入本專案

複製範例 YAML 到執行資料目錄:

```bash
mkdir -p data/data_sources
cp docs/examples/custom-data-source/mock_source.yaml data/data_sources/mock_source.yaml
```

然後在面板裡開啟:

```text
設定 -> 資料源 -> 重新載入
```

選擇 `mock_source` 後,可用「試拉測試」驗證 `daily`、`adj_factor` 和 `realtime`。

完整說明見 [../../custom-data-source.md](../../custom-data-source.md)。
