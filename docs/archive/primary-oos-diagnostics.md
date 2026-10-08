# Primary OOS 診斷

## 結論

目前的 Primary OOS 結果不支持把模型升級成 LightGBM。正式評估的點估計可重建，負 IC 不是分數方向或資料切分錯誤。主要問題是三個動能因子同質性高、等權組合稀釋較有效的長週期訊號、每日橫斷面排序不穩定，以及不同年份的效果反轉。

本文件只診斷既有正式 OOS run。所有消融與候選比較都使用已看過的 OOS 資料，因此只能作為下一版設計依據，不能當成新的乾淨 OOS 證據。

## 可重現範圍

- Formal run ID: `2538dbd6-acc5-43ae-9061-a74149aeb9f1`
- Dataset identity: `c0336dd5980a165bfb0fa1fa99b7e5438ea4bd862ade90e17b73210296e57626`
- Formal code SHA: `7ceb74dcd6245fd2a2a79facc363c5c96edd9968`
- Formal specification hash: `7062a326c3e6006b360b4b15a116079c4ae3f8ba316fcf06396dae7b1d8c1cca`
- OOS dates: 2018-10-24 through 2026-08-04
- Folds: 30
- Admitted rows: 1,847,139
- Complete-case rows: 1,743,637
- Diagnostic identity: `51e80f4b8617a49515d93862e50f45e1b066c96305abc1fb5823e9fe362e3f7b`

執行方式：

```powershell
Set-Location 'E:\Git\台灣股票\TWstockfor_tick-stock-panel\backend'
uv run --frozen python scripts/taiwan_primary_oos_diagnostics.py
```

程式只讀正式 run store、既有 OOS partitions、觀測到的價格資料和已驗證 corporate actions。它不重跑 A2B、不重建 factor panel、不寫入正式 run ledger。機器可讀輸出位於 `data/user_data/primary_oos_diagnostics/<run_id>.json`，此目錄不納入 Git。

## 指標重建

所有三個因子的契約方向都是 `higher_is_better`。Production scorer 先對每日橫斷面做 ordinal percentile rank，再以固定等權算術平均建立 composite score。完整重建後，5D 和 20D 的 composite IC、top return、bottom return、long-short return 都與正式 artifact 在絕對誤差 `1e-12` 內一致。

| Horizon | IC mean | IC median | IC std | ICIR | Positive IC ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 5D | -3.2431% | -2.9054% | 14.6098% | -0.2220 | 41.16% |
| 20D | -1.4626% | -0.0693% | 13.5410% | -0.1080 | 49.63% |

負 IC 不是正負號解讀錯誤。契約明確表示高分應預測較高 forward return，但實際每日全橫斷面排序平均為負。

## 分層與 benchmark

正式 top/bottom bucket 使用每日分數排序後各取 `ceil(20%)`。下表的 0050 差異保留正式報告的「兩個全期平均值相減」口徑；配對日期口徑也同樣為負。

| Horizon | Top | Universe | Bottom | Long-short | Top minus universe | Top minus 0050 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5D | 0.5313% | 0.3790% | 0.3455% | 0.1857% | +0.1523% | -0.0253% |
| 20D | 2.1396% | 1.5240% | 1.2010% | 0.9386% | +0.6156% | -0.0868% |

Top bucket 優於同日 universe，bottom bucket 也低於 universe，因此 long-short 並非只靠弱勢股下跌。不過 top bucket 沒有超越 0050。只比較共同日期時，top minus 0050 仍為負，5D 是 -0.0230%，20D 是 -0.0659%。

聚合後的 quintile 平均報酬看似上升，尤其是 20D；但每日嚴格單調比例只有 5D 8.99% 和 20D 11.01%。Top quintile 是當日最佳 quintile 的比例也只有 39.63% 和 42.28%。這解釋了正 long-short 與負全橫斷面 IC 可以同時存在：尾端平均有效，但日常中段排序和整體 rank 關係不穩定。

## 因子與消融

每個單因子的 raw IC 都為負，且方向調整後不變。

| Factor | 5D IC | 20D IC |
| --- | ---: | ---: |
| `momentum_5d` | -3.6036% | -1.0112% |
| `momentum_20d` | -2.4836% | -2.2777% |
| `momentum_60d` | -1.5530% | -0.6313% |

在固定 complete-case universe 上，移除 `momentum_5d` 後，5D top return 從 0.5313% 升到 0.5605%，long-short 從 0.1857% 升到 0.2522%；移除 `momentum_20d` 後，20D IC 從 -1.4626% 改善到 -0.8130%。`momentum_60d` 單因子在這組事後比較中最好，5D/20D long-short 分別為 0.2687% 和 1.1250%，配對日期的 top minus 0050 分別為 +0.0093% 和 +0.0073%。這些結果只適合產生候選假說，不能用來宣稱新模型通過 OOS。

因子之間的平均每日 Spearman correlation 為：

- `momentum_5d` vs `momentum_20d`: 0.4222
- `momentum_5d` vs `momentum_60d`: 0.2286
- `momentum_20d` vs `momentum_60d`: 0.4984

三個欄位都是同一家族的動能訊號。固定等權不只沒有增加足夠的獨立資訊，也會讓較弱的短週期訊號稀釋長週期訊號。

## 時間穩定性

5D composite IC 在每個曆年都為負。20D composite IC 只在 2023、2024 和 2026 為正。5D 和 20D long-short 都在 2018 與 2022 為負。`momentum_60d` 雖然是本次事後比較中較好的候選，20D IC 仍會在不同年份反轉。因此目前證據較符合 regime-sensitive signal，而不是一個可直接增加模型複雜度的穩定關係。

## 完整性與集中度

- Complete-case 排除 103,502 rows，占 admitted rows 的 5.60%。各單因子在自己的可用 universe 與固定 complete-case universe 間，IC 差異絕對值不超過 0.00063。缺值選樣存在，但不是主要根因。
- 493,114 rows 位於重複 composite score 群組，占 complete-case rows 的 28.28%。Bucket boundary 以 canonical symbol order 決定，Spearman 使用 average ties。這是需要保留監控的離散化特徵，不是本次重建誤差。
- Top bucket 位於最高價格 quintile 的比例約 27.6%，位於最高 20-day liquidity quintile 的比例約 33.2%。有流動性與價格傾斜，但沒有單獨解釋目前 IC 問題。
- 正式 panel 沒有 point-in-time historical industry labels，也沒有 point-in-time share-count series。Industry 和 market-cap concentration 結論都是 `data_insufficient`。
- 正式 scoring 沒有 clipping 或 winsorization。

## 根因分類

| Code | 判斷 | 證據 |
| --- | --- | --- |
| A | 否 | 契約與 scorer 都是高分預測高報酬，負 IC 是真實結果。 |
| B | 是，屬因子假說弱化 | 三個 raw factor IC 全期皆負，短週期最弱。未發現公式或 PIT 實作錯誤。 |
| C | 是 | 固定等權稀釋 `momentum_60d`，消融短週期因子可改善部分指標。 |
| D | 是 | 每日嚴格單調率只有 8.99%/11.01%，top-best ratio 只有 39.63%/42.28%。 |
| E | 部分 | Top 優於 universe，bottom 低於 universe；但 top 未超越 0050。 |
| F | 是 | 年度 IC 與 long-short 有明顯 regime reversal。 |
| G | 是 | 三個動能 horizon 彼此正相關，資訊重複。 |
| H | 否，非主要根因 | 缺值排除率 5.60%，固定 universe 前後 IC 變化很小。 |
| I | 部分且資料不足 | 價格與流動性有中度傾斜；industry/market-cap 無 PIT 資料。 |
| J | 否 | 已有足夠證據支持多重、可定位的原因。 |

## 下一版決策

決策是 `NOT_YET`，不要在目前證據上導入 LightGBM。

下一版先採最小、可預先登記的設計：

1. 凍結 v1 與本次診斷，保留為正式參考。
2. 為 `primary-oos-v2` 預先登記很小的候選集合，例如 v1 等權、`momentum_60d` 單因子，以及排除 `momentum_5d` 的雙因子版本。
3. 每個 outer fold 只能用 train/validation 選方向與候選。不得用該 fold test 或這份全期診斷調連續權重。
4. 2018-2026 的 nested rerun 必須標成 retrospective diagnostic，不得重新命名為 fresh OOS。
5. 保留 2026-08-04 之後第一個完整 63-session block 作為 confirmatory untouched OOS。它完成前不升級 production model。
6. 只有簡單候選在 untouched block 同時得到穩定正 IC、正 top-minus-universe、以及不劣於 0050 的 top return，才重新評估 LightGBM。

LightGBM 只能在簡單候選已建立穩定訊號後處理非線性。如果在目前負 IC、regime-sensitive、同質因子組合上直接增加模型容量，只會擴大事後選擇與過度擬合風險。
