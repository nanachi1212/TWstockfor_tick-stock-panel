# A5.1 / A5.2 / A6 資料正確性契約

日期：2026-09-23。以目前程式、離線測試、A1/A1.5 audit 與本輪有限官方查詢為準。
本輪沒有啟動 LongRun、正式 OOS、ML、Portfolio、UI，也沒有回寫既有市場資料。

## 1. 交付與規則

- 起點：`145e5b47d40f4cc7a8a5247790ffc96be1712296`。
- 使用者修改的 `AGENTS.md` 已獨立提交：`80bdb3d153f0f4d3ff9a2d4e4e9078fa82176621`。
- 工作分支：`feat/taiwan-quant-foundation`。
- 本輪採明確指定的 commit / push 流程，不開 PR、不合併。因此 CI / Codex GitHub Review 未執行。
- 專案預設流程只在未指定流程時適用，與本輪「不要開 PR」沒有衝突。

## 2. TradingDayEvidence

`app/taiwan/realtime/calendar.py::TradingDayEvidence`：

| 欄位 | 定義 |
| --- | --- |
| `date`, `exchange` | 證據對應的日期、TWSE / TPEX |
| `status` | `trading` / `non_trading` / `unresolved` |
| `evidence_source` | 官方 snapshot、已驗證日曆或 `calendar_rule` |
| `reason` | 有效市場列、已驗證休市、週末、待處理、無法解釋的空資料、provider error、schema mismatch |
| `retrieved_at` | 有時區的取得時間；規則、pending、缺少時間的舊 metadata 為 null，不捏造時間 |

有效官方市場列是 trading；known_holidays 是 non_trading；週末可以由 calendar_rule
確認 non_trading。平日不是已驗證交易日，HTTP 成功也不是有效市場資料的證明。
明確交易日證據優先於週末規則；known_holidays 與 known_trading_days 衝突則 unresolved，
`is_trading_day()` 與 `day_evidence()` 使用同一判定。
空 payload / 舊空 Parquet 一律 unresolved；schema mismatch / provider error 不建立完成分區，
讓 worker 下次仍可重試。失敗原因保存在既有 checkpoint，worker status 以
`failed_date_evidence` 輸出正式證據。

成功分區的證據存在同一 Parquet footer，與資料一起 atomic replace，沒有另一份 completion manifest。
舊非空分區可讀為 legacy market observations；舊空分區維持 unresolved，不要求刪資料。
本輪沒有宣稱已找到完整官方歷史休市日來源。

## 3. 兩種 denominator

```text
processed_ratio = processed_dates / candidate_dates

expected_trading_sessions = verified trading + unresolved possible sessions
                         = candidates - confirmed non-trading
trading_coverage_ratio = observed_trading_sessions / expected_trading_sessions
```

worker 的 candidate 集合保留原本的候選平日，包括已確認假日；處理進度與 Quant 覆蓋率獨立。
未處理平日、未知原因空結果都留在 Quant 分母。已有 calendar trading 證據但缺 snapshot
的日子仍在分母，不能算 observed。

離線回歸：3 個候選日中 2 個官方確認休市、1 個有效市場 snapshot，全部處理後：
`processed=3/3`、`observed=1`、`expected=1`、`trading_coverage_ratio=1.0`。

## 4. Primary 與 Secondary

`health_from_stores()` 只使用 TWSE 的 coverage、as-of 分類與各層門檻決定 Primary。
不取 TWSE / TPEx 完成日交集。

- `primary_twse`：獨立 coverage、classification counts、readiness、blocked_reasons。
- `secondary_tpex_experimental`：獨立 coverage、observed experiment readiness；
  `instrument_type_status=data_insufficient`、`ready_for_verified_oos=false`。
- TPEx 不完整或類型來源 BLOCKED，永遠不提升或阻擋 TWSE Primary。
- 舊 `census` / `census_ratio` 是 TWSE Primary 的相容別名。
- 一般 framework readiness 可為 true；這不構成正式 OOS 可用宣告。

## 5. 31 筆 unresolved 的官方排除證據

2026-09-23 僅查詢已知的官方歷史分表，參數皆為 `date=20150105`：

| 官方 type | 歷史表標題 | 筆數 | 與該日 census 交集 | 最終類型 |
| --- | --- | ---: | ---: | --- |
| `019919T` | 受益證券 | 6 | 6 | `beneficiary_security` |
| `9299` | 存託憑證 | 25 | 25 | `tdr` |

兩集合合計 31 筆，正好解釋原本 `911 - 855 - 25` 的缺口。
這些是 `instrument_type_status=verified`、`primary_oos_eligible_type=false`，不是分類失敗。
存託憑證表首欄是「暫停交易」，代碼必須按「證券代號」欄名讀取，不能讀 `row[0]`。
來源：[TWSE 歷史每日收盤行情](https://www.twse.com.tw/zh/trading/historical/mi-index.html)。
精確 API 為 `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX`，參數
`date=20150105&type=019919T&response=json` 與 `type=9299`。

### 舊 audit 的普通股矛盾與保守修正

`taiwan-historical-universe-probe.md` §3 明寫產業聯集含特別股，且原本 962 / 28
的拆分使用了代碼格式；§4.3 卻把整個產業聯集當普通股，兩者矛盾。
本輪確認 2015-01-05 金融保險歷史回應只有行情欄位，沒有普通股／特別股欄位；
`0999GA` 的明確語意是「附認股權特別股」，不能用這個子集的空表證明整個產業聯集沒有特別股。
因此不使用碼長、字首、名稱或現在的 master 修補。

**原先的 855 筆只取得產業成員證據，不能維持 verified common-stock 的宣告。**
原始 Parquet 不變；讀取舊 `industry_tables@date` 紀錄時，在記憶體中將
`instrument_type=null` / `classification_status=data_insufficient`，保留來源供稽核。
新 classifier 同樣保守。這是 Primary 正確性 BLOCKED，不是 TPEx 造成的阻擋。

2015-01-05 的最終 audit 統計（把新查詢的 31 筆在記憶體中合併，未寫入 production store）：

| 統計 | 數量 |
| --- | ---: |
| observed | 911 |
| verified_stock（V1 普通股） | 0 |
| verified_etf | 25 |
| verified_unsupported | 31 |
| unknown | 855 |
| industry_only_unresolved | 855 |

這與「原有 31 筆無法分類」是兩件事：31 筆已由官方 resolve；855 筆原有普通股宣告缺少充分證據。
未知列不得因門檻需要而升級。

## 6. Primary classification denominator

```text
denominator = verified_stock_count + unknown_count
ratio = verified_stock_count / denominator
unknown_ratio = unknown_count / all observed securities
```

ETF 與已驗證 unsupported 排除。空分母不宣告 readiness。
統計只使用查詢區間內觀察到的 code，分類必須在該 code 於區間中的首次使用日以前成立；
未來分類不能補成過去已知。另保留 `industry_only_unresolved_count` 與明確 blocked reason。
本輪也修正 PIT 查詢順序：先按 as-of 篩分類，再取當時最新紀錄；
新增未來分類不再遮掉原本已驗證的較早分類。
若當時最新分類是 unresolved，也不得沿用更早的 verified；同日互相衝突的類型一律 unknown。

## 7. 分類舊資料與 worker 相容

每個分類日期新增 2 次官方分表請求，37 = 34 產業 + ETF + 受益證券 + 存託憑證。
新分區 footer 有 contract version 2；舊分區仍可讀，並在既有分類 queue 中列為待升級。
下一次由使用者執行 worker 時，使用既有 budget / retry / rate limit 更新對應分類日期。
不清除 census、日 K、checkpoint，不要求從頭回補。本 session 未跑此資料更新。
非 OK 的 provider error 不得當成空類別，失敗日期不建立完成分區。

## 8. Canonical corporate-action store

`CorporateActionStore` 預設為 `<DATA_DIR>/taiwan/adj_factor/events.parquet`，沿用
`taiwan_data_root()`；不引入 database engine，不 import 或寫入 daily store。

```text
symbol, exchange, effective_date, effective_at, event_type
previous_close, reference_price, factor
cash_dividend, free_share_ratio, reduction_ratio
source, source_url, retrieved_at
status, precision_method, revision_status
raw_fields, reason, available_at, availability_policy
```

價格／比例為 Float64，計算先用 Decimal 保留官方高精度；raw_fields 保存原始參考價與明細。
`reference_price` 是 factor 真正採用的高精度價格基準，不冒充原表截斷到兩位的小數。
缺少的 cash / ratio 保持 null，尤其不從減資價格反推 reduction_ratio。

寫入先合併再 mkstemp + os.replace；exclusive lock 防止多 process lost update。
鎖衝突直接失敗，不自動刪 stale lock。無記憶體讀取 cache。
相同事件內容冪等；不同版本的來源內容保留為多個 observation，讀取時同 symbol/date
衝突一律回 `data_insufficient` / `revision_status=conflict`。
官方沒有 revision timestamp，不能宣稱版本發布時間已驗證。

## 9. 因子的 authoritative source 與精度

| 事件／來源 | 價格基準與精度路徑 |
| --- | --- |
| TWSE 除權息，兩官方參考價相同 | `C_prev - 權值+息值`；保留官方高精度，再除以 C_prev |
| TWSE 除權息含現增，兩參考價不同 | 必須取 TWT49UDetail 的現金股利與每千股無償配股；重算 `(C-cash)/(1+free)`，截斷後與官方減除股利參考價核對 |
| TPEx exDailyQ | 直接使用現金股利與每仟股無償配股重算，同樣核對；不使用現增理論除權參考價 |
| TWSE TWTAUU / TPEx revivt | 官方恢復買賣參考價 / 官方停止買賣前收盤價 |
| TWSE TWTB8U | 官方恢復買賣參考價 / 官方停止買賣前收盤價 |

parser 與 `derive_factor()` 分離。TWSE 權息合併事件記為一筆 stock_dividend，
價格因子已包含現金部分，不拆成兩筆相乘。純現增經明細證明 cash=free=0 時，
記錄為 `cash_capital_increase`，factor=1，避免 audit 6658 的假報酬。

## 10. Fail-closed 與仍缺證據項目

- 缺 previous_close / reference_price、`-`、尚未定價、非有限數值、非正價格、參考價不符：data_insufficient。
- 含現增但無明細、unsupported event、TPEx par_change：data_insufficient。
- schema / identity mismatch：整批拋出 `CorporateActionSourceError(status=provider_error)`，不接受部分成功。
- 明細 HTTP 失敗：該事件 provider_error，不退回低精度猜值。
- ambiguous duplicates、同日跨來源衝突、更正衝突：拒用，不擅自合成。
- volume adjustment、未驗證的 reduction ratio、predictive available_at：仍 data_insufficient。
- TPEx revivt 沿用已驗證欄位比值，不宣稱已逐筆重算所有減資公式。

## 11. effective_at 與 available_at

除權息是 ex-date 09:00 Asia/Taipei；減資／面額變更是復牌日 09:00。
`available_at=null`，`availability_policy=market_mechanism_inferred`。
normalization 使用 effective_at；未來的公告配息、即將除息特徵仍需要另外取得
verified available_at。沒有把此例外加到 fundamentals、revenue、institutional、news。

## 12. PIT API

```python
adjust_prices_as_of(history, *, as_of, events,
                    price_columns=("open", "high", "low", "close"))
```

回傳 `PITAdjustedPrices`。每個 raw row t 只乘 `t < event_effective_date` 且
`event_effective_at <= as_of` 的因子。`as_of=date` 表示該日 regular close；
datetime 必須有時區，日 K 只在該 session close 之後可見。
事件缺證據時受影響價格為 null，整個 anchor window 為 data_insufficient，不能訓練。
raw OHLCV、volume、amount 與來源 Parquet 都不修改。

事件集合必須由 caller 明確提供，表示該 window 的事件 snapshot。空集合只表示
caller 指定沒有事件，**不證明未抓取 store 的事件完整性**；本 API 不做 readiness 宣告。
加入 2024／2026 事件不改變 frozen 2018 as-of fixture。

## 13. 展示與訓練隔離

`adjust_prices_for_presentation()` 回傳另一個型別 `PresentationAdjustedPrices`。
兩型別匯出 Polars 時保留 `usage_scope / adjustment_as_of / adjustment_status`。
indicator panel 與 training_matrix 拒絕 presentation wrapper 和其匯出 frame。
indicator panel 保留 PIT provenance；training_matrix 也拒絕用 T 的視窗冒充 t<T 的特徵。
每個歷史特徵日期須有自己的 as-of window。既有未標記 raw feature frame 保持相容。
indicator window 拒絕混用多個 as-of anchor；latest-row wrapper 也保留 PIT provenance。
舊 `price_semantics=adjusted` frame 若沒有 PIT provenance，不得進 training_matrix。
market regime / breadth 拒用 presentation；regime 排除 as-of 之後的價格，並拒用
data_insufficient adjustment，breadth 則排除該類無效列，沒有有效列時回 null。

## 14. Forward label

```python
forward_adjusted_return(history, *, start_session, horizon_sessions, events)
```

單一 symbol、依提供的實際 session 列數選 T+H，不用日曆天數。
caller 必須提供完整且已確認的 session history；primitive 不猜缺少的交易日。
兩端都是收盤，事件區間為 `(close(T), close(T+H)]`：
T 當日開盤事件已反映在 raw close，排除；T+H 開盤事件納入；之後事件忽略。
使用同一個 normalization 實作、以 T+H 錨定，回傳已實現的價格正規化報酬，
不是股利再投入總報酬，也不是 feature 可得性例外。

## 15. OHLC 支援矩陣

依 A1 §2.5 已驗證的價格基準語意，同一日 OHLC 使用相同因子：

| 事件 | TWSE O/H/L/C | TPEx O/H/L/C |
| --- | --- | --- |
| cash_dividend | 支援 | 支援 |
| stock_dividend（含權息） | 支援 | 支援 |
| capital_reduction | 支援 | 支援（官方參考價比值） |
| par_change | 支援 | data_insufficient |
| 已驗證純現增 no-op | factor=1 | factor=1 |

任何 source / factor 本身未 verified，整組仍不得計算。volume 不在支援矩陣。
漲跌停判定仍使用 raw price；本輪不更動 current product 價格語意。

## 16. Volume 邊界

`window_crosses_share_count_event()` 採 `start_session < event_date <= end_session`。
股票股利、減資、面額變更與無法確認的事件使 share-volume window unavailable。
`volume_window_status()` 對 relative_volume / volume_ma / volume_momentum 回
data_insufficient；amount / ADV20_TWD 不受影響。
PIT frame 攜帶股數基準 epoch；indicator panel 的 vol_ma5 / vol_ma10
若跨基準就輸出 null + data_insufficient，完整視窗落到新基準後恢復。
沒有提供或推導 volume-adjustment 公式。

## 17. 驗證結果

實際命令：

```text
uv run --frozen pytest -q tests/test_taiwan_corporate_actions.py tests/test_taiwan_pit_adjustment.py tests/test_taiwan_data_evidence.py tests/test_taiwan_observed_universe.py tests/test_taiwan_pit_universe.py tests/test_taiwan_quant_framework.py tests/test_taiwan_indicator_panel.py tests/test_taiwan_technical_indicators.py tests/test_taiwan_realtime.py tests/test_taiwan_daily_store.py tests/test_taiwan_data_root.py tests/test_taiwan_dividend_events.py
uv run --frozen pytest -q
uv run --frozen ruff check <所有本輪修改 Python>
git diff --check
```

定向測試：PASS，282 passed。新增 3 個測試檔，共 94 個參數展開後的測試案例；
其中保留並整合使用者同意納入的 13 個額外回歸案例。另修改 4 個既有分類測試，
使斷言符合官方產業資料無法驗證普通股 subtype 的證據，沒有移除測試。
完整 pytest：**1942 passed / 7 failed / 63 warnings**（239.43 秒）。
與修改前完整 baseline 的 7 個 failure node ID 逐一比對，集合完全相同；新增失敗 0。
完整 suite 狀態仍為 FAIL（既有失敗），不宣稱全綠。修改後的本工作包定向測試為 PASS。
所有 16 個本輪修改／新增 Python 檔案 Ruff PASS；`git diff --check` PASS。
沒有使用 --ignore 或 --continue-on-collection-errors。Repo 沒有設定後端 mypy gate，未新增工具／依賴。

修改前完整 baseline：1848 passed / 7 failed / 63 warnings。失敗集合：

```text
tests/test_intraday_monitor_signals.py::test_intraday_batch_provider_is_normalized_without_network
tests/test_kline_sync_timezone.py::test_fetch_minute_single_window_is_beijing_wall_clock
tests/test_taiwan_ai_research.py::test_valid_grounded_report_accepted
tests/test_taiwan_ai_research_validation.py::test_financial_stock_and_leveraged_etf_semantics
tests/test_taiwan_ai_research_validation.py::test_zero_is_not_missing_or_unavailable
tests/test_taiwan_daily_update.py::test_api_taiwan_data_status_endpoint
tests/test_taiwan_enrichment_live_smoke.py::TestTaiwanEnrichmentLiveSmoke::test_etf_classification_and_bridge_validation
```

有限官方 smoke：TWT49U 6658 2025-01-06 factor=1；2025-03-18 15/15 verified
（含 2330 高精度 0.9953608041237113）；TWTAUU 2412 2011-01-25 factor=88.87/73.1；
TWTB8U 8070 2020-08-17 factor=0.1；TPEx exDailyQ 2025-06-13 11/11 verified；
revivt 3064 2024-02-05 factor=3.3333333333333335。
這是有界唯讀查詢，不在 unit tests 中，不寫 production data，也不代表完整歷史覆蓋。

## 18. 尚未解除的正確性限制

1. TWSE 普通股／特別股 historical subtype 的 authoritative 證據不足，Primary readiness BLOCKED。
2. 完整歷史 holiday evidence 未備齊；未解日期留在 denominator。
3. TPEx historical type、par-change、volume adjustment 仍 data_insufficient。
4. 官方事件表無 revision publication timestamp；衝突拒用，不宣稱嚴格 vintage 已知。
5. 本輪不宣告事件或 session 資料已全量回補，也不宣告任何正式 OOS／模型績效。

## 19. B0 最後一次普通股 subtype 窄範圍核對（2026-09-23）

只核對既有 audit 指向的官方來源，沒有擴大掃描端點：

- TWSE `MI_INDEX` 歷史 `ALLBUT0999`／產業分表提供交易與產業成員，不提供普通股／特別股 subtype；產業分表同時包含特別股，見本檔 §5 與 [官方歷史日報](https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20150105&type=ALLBUT0999&response=html)。
- [TWSE 官方 ISIN/CFI 名冊](https://isin.twse.com.tw/isin/C_public.jsp?strMode=2)有證券種類與 CFI 欄位，但目前查詢是現況名冊；沒有已驗證的 2015 as-of snapshot，也無法保證已下市證券及 listing episode 的歷史 subtype 完整可得。不可把今天的 CFI 回貼 2015。
- 既有 probe §9.2 的 MOPS 公司基本資料是現況登錄名冊，沒有已驗證的歷史 as-of 參數；公司資料亦不能代替每一檔證券的 subtype 證據。
- TWSE [股票種類說明](https://www.twse.com.tw/zh/products/securities/stocks.html)確認普通股與特別股為不同權利類別；不能以產業成員身分當成普通股證明。

結論：未找到可對 2015-01-05 全部 855 筆產業成員提供 authoritative、historical/as-of-safe 普通股 subtype 的官方資料。`verified_stock` 維持 unresolved；**Primary TWSE Verified OOS remains BLOCKED by historical common-stock subtype evidence.** 不採代號、名稱、現況 master/CFI 或產業表推論解鎖。此限制不阻擋 B2/B3 framework 的 synthetic/experimental dry-run。

## 20. B2/B3 framework 契約

`quant/panel.py` 對每個 feature date T 以 T 收盤為錨點調用 `adjust_prices_as_of`，技術與股票報酬只讀 PIT 調整價。法人、融資券與市場指數列須有不晚於 T 收盤的明確 `available_at`；目前舊 store 沒有這個欄位時，因子保留 null 與 coverage exception，不回填零。歷史產業因子固定 `not_pit_safe`。股數基準事件跨 20 期時 `relative_volume` 為 `data_insufficient`，金額與 ADV20 不受此限制。

`quant/training.py` 是 panel 進入模型列的入口：`resolve_training_eligibility(manifest, capabilities)` → `quant_eligibility.eligible(universe, policy)` → `training_matrix()`。每日期的候選母體限同一 `policy_version`、`universe_tier` 的 eligible symbols，輸出保留 `feature_schema_version`、`factor_version`、`policy_version`、`universe_tier`，並回報 rejected features/symbols 與理由。`cross_section.py` 只對此母體做 winsorize、zscore、rank_pct，percentile 附 `date/policy_version/universe_tier/eligible_count`；產業中立化拒絕無 PIT assignment。

`factors/factor_version=*/policy_version=*/universe_tier=*/date=*/` 分區含 `values.parquet`（identity/provenance + factor values）及 `coverage.parquet`（只含非 available exception：`symbol/date/factor/status/as_of/available_at/reason/source`）；`factors/_factor_meta.json` 保存 formula、unit、source、min_history、price semantics 與 manifest/capability training requirement。整個分區以暫存目錄原子發佈；既有不同內容不能覆寫，須換版本。

`quant/baseline.py` 只讀已入選矩陣，train window 計 IC/ICIR 與權重，validation window 選 threshold，test 僅排名。所有輸出標記 `usage_scope=experimental_only`，不寫 formal Primary OOS artifact；歷史產業與未通過 capability × manifest 的因子不能進權重。

## 21. B0/B2/B3 驗證（2026-09-23）

- 定向：`tests/test_taiwan_factor_panel.py`、`test_taiwan_quant_framework.py`、`test_taiwan_pit_adjustment.py`、`test_taiwan_technical_indicators.py`，**95 passed**。含 2018 factor hash 在追加 2024 event 後不變、未來 index/available_at 不洩漏、股數事件 volume 邊界、政策母體與排序、訓練 admission、fold train/validation/test 隔離、storage 冪等及原子發佈失敗案例。
- 完整 `uv run --frozen pytest -q --tb=line`：**1954 passed / 7 failed / 63 warnings**（234.02 秒）。七個 failure node ID 與本檔 §17 的基線集合完全相同；新增回歸 0。完整 suite 仍為 FAIL，不能稱全綠。
- 一次完整 suite 額外出現 A 股 worker 子程序 native exit `3221225477`，同一 mining 案例單獨連續三次通過，下一次完整 suite 未重現；未修改或忽略該測試，列為間歇性環境現象。
- 本輪 Python 檔案 Ruff 通過；未執行 CI / GitHub Codex Review，因本工作包明確不開 PR。未產生正式 OOS artifact。
