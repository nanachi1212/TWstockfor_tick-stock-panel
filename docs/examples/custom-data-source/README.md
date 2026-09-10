# 自定義數據源 mock 聯調示例

這個目錄提供一個本地 mock HTTP 數據源,用於驗證項目的自定義數據源接入鏈路。

## 運行 mock 服務

```bash
cd docs/examples/custom-data-source
python mock_server.py
```

服務默認監聽:

```text
http://127.0.0.1:3021
```

端點:

| 端點 | 數據 |
| --- | --- |
| `/daily` | 日K |
| `/adj_factor` | 除權因子 |
| `/realtime` | 全市場實時快照 |

## 接入項目

複製示例 YAML 到運行數據目錄:

```bash
mkdir -p data/data_sources
cp docs/examples/custom-data-source/mock_source.yaml data/data_sources/mock_source.yaml
```

然後在項目裡打開:

```text
設置 -> 數據源 -> 重新加載
```

選擇 `mock_source` 後,可用「試拉測試」驗證 `daily`、`adj_factor` 和 `realtime`。

完整說明見 [../../custom-data-source.md](../../custom-data-source.md)。
