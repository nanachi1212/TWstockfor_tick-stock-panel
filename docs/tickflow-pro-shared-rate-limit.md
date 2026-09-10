# TickFlow Pro 限頻安全預算（進程內 + 跨產品錯峰）

## 決策（經 ChatGPT/Codex 評析修訂）
- `backend/app/tickflow/rate_limits.py` 提供**單 Python 進程內**的 rpm 槽位限速與 `SAFETY_RPM_FACTOR=0.8`。
- **不要**把「各進程各扣 80%」當成賬戶級共享限頻：Gold Shadow 容器與 A 股面板進程狀態獨立，理論聚合可達 160%。
- Stage A 期間跨產品靠**錯峰**，不在 Gold 上部署分佈式限頻重構。

## 預算表示例（Pro，單進程 80%）
| capability | 套餐 rpm | 進程內 80% |
|---|---:|---:|
| quote.batch | 120 | 96 |
| quote.pool | 60 | 48 |
| kline.daily.batch | 60 | 48 |
| kline.minute.batch | 30 | 24 |
| depth5.batch | 30 | 24 |
| adj_factor | 60 | 48 |

## 錯峰（Stage A）
- 盤中：優先 Gold Shadow 觀察與 legacy `gold-monitor`。
- A 股 Pro 探測/大批量同步：建議 **16:00 後**。
- 禁止全市場一年分鐘一次性回填。

## 實現要點
- `resolve_limit(..., apply_safety=True)` 默認對 rpm 做 `floor(rpm * 0.8)`。
- `sleep_between_batches`：`index=0` 只佔槽不 sleep（首批突發；**併發多個 index=0 仍可能超 rpm**）；後續 batch 按槽位等待。
- Phase 1 / Stage A：**不要併發**啟動多個 probe 或大批量 sync。
- 診斷可傳 `apply_safety=False`；跨容器賬戶預算需另設（Stage A 不做）。
- 任一 429 / fallback 應記入證據鏈，禁止靜默混源。
