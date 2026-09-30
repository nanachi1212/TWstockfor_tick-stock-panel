# 策略實驗室 v1

以 `e2d03f7` 為開發基線，將已保存的 forward observations 整合到 `/strategy-lab`。屬 L3 的小範圍產品接線：複用 Selection Review 的評估器、Daily recommendation 的 LiveLedger，以及既有 API client、查詢鍵與導覽；沒有新增回測引擎。PR 保留 draft，待 A / B merge 後才吸收 main；不 merge、不要求 Codex Review，不依賴或修改 PR #33。

## 資料與資格

- Selection：只讀取正式 `forward_batch`。檢查來源日期、策略版本、規則、行情覆蓋、風險來源、公司行動摘要，以及收盤後、下一個預定交易日開盤前的保存與鎖定時間。收益沿用 Selection Review 的下一交易日紙上開盤進場與 1D / 5D / 20D 收盤。一般 research snapshots 不納入。
- A13 Buy Point：後續由既有伺服器保存動作附上 `a13_server_observed` 與策略定義 SHA-256；保留訊號日原始參考收盤基準與既有收益口徑。設定條件、風險過濾等定義不同時分開統計。舊快照缺少該證據，或保存時間已超過前瞻窗口，全部 horizon 列 unavailable，不補寫舊快照。新增的正式觀察不可刪除。
- Daily recommendation：只讀 LiveLedger 的 immutable signals 與已 append 的 outcomes，檢查快照內容摘要、live contract、訊號日、audit、有限報酬及 outcome 日期。`verified` 投影為 matured，`pending` 保留 pending，資料不足、衝突或 recheck unavailable 投影為 unavailable。使用 SQLite read-only 連線，缺少 outcomes 檔案時不建檔。
- Strategy identity 保留來源、strategy id、version、設定摘要、entry basis、價格語意與成本假設。consensus、breakout、trend、Daily recommendation、A13 不混合。策略名稱變更不改變 identity。
- 同一 snapshot id 的相同內容只計一次，內容衝突整份排除。同一 identity、signal date、symbol 的重存觀察也只計一次；內容衝突列 unavailable，provenance 保留重複 snapshot ids。

所有結果標示 `Forward / OOS observed`，此處 OOS 是保存訊號後的前瞻觀察，不表示已通過 primary historical OOS 驗證。頁面固定顯示 `Historical PIT research：未完全可用`，不讀取未驗證 historical PIT 評估或聲稱 historical backtest performance。

## 統計口徑

| 項目 | 定義 |
| --- | --- |
| 樣本數 | 去重後的 snapshot / symbol 觀察，跨 horizon 不重複加總 |
| N、matured | 該 horizon 可驗證且報酬有限的成熟觀察數 |
| hit | 未四捨五入報酬 `> 0`，0 不算命中 |
| hit rate | hit / N × 100，pending、unavailable 完全排除 |
| avg return | N 個觀察等權算術平均；不是投資組合報酬 |
| benchmark excess | 個股報酬減同期間、同進場基準的 benchmark 報酬，再取配對樣本平均，另列 N |
| minimum sample | 預設 5，API 最低允許 5；每個策略、切片、horizon 各自檢查。未達門檻只顯示計數與不可用狀態 |

報酬與超額以百分數輸出，daily ledger 的小數報酬僅在邊界乘 100。正式 Selection 的 benchmark 為 0050；A13 延用既有原始價評估。Daily ledger 尚未保存 benchmark，顯示不可用，禁止改拿其他策略的 benchmark 或今天資料補值。皆未扣成本、滑價，不保證成交；公司行動價格正規化不等於含股息再投資總報酬。

## 切片、比較與明細

可依 strategy、selection source、TWSE / TPEX 與保存的 risk status 篩選。industry、liquidity bucket 沒有正式保存背景時停用；Daily 的 regime 目前只保存 partial context，不能當 verified regime 切片，也不從今天的產業／流動性／大盤資料回填。

勾選 2–4 個策略並排顯示客觀統計，以穩定 identity 排序，無 winner ranking。每個策略可展開逐筆觀察：symbol、signal date、entry、horizon、return、benchmark、excess、status、identity、snapshot id、as_of、outcome date 與 provenance，並可跳至該筆 Selection Review 或 Stock Detail。明細每頁 50 筆。

Selection provenance 包含 snapshot 內容摘要、鎖定證據、公司行動／營收摘要、評估時間及評估輸入版本摘要（檔案版本與 calendar；不是宣稱每根行情的原始供應商雜湊）。Daily provenance 包含 snapshot、feature、raw history、appended outcome 摘要與 audit 狀態。

## 相容性與快取

既有快照僅新增可選欄位，不 migration、不重新寫入 immutable snapshots。SnapshotReviewDetail 新增 outcome date 與原始 benchmark / excess，既有四捨五入顯示不變。Selection Review 的共享評估快取增加 pending 結果，於市場收盤階段、交易日、價格或證據版本變更時失效；公司行動 window 快取綁定本機證據版本。前端篩選載入期間保留選項，隱藏上一條件統計。

不加入價格同步、signal reconstruction、SSE 寫入或新的持久化資料流。回滾可 revert 此功能 commit，既有快照欄位向後相容，不需刪除使用者資料。

## 畫面驗證

以下為固定測試樣本，畫面內各策略名稱標示「測試樣本」，僅證明介面與契約。新頁面沒有對應的舊版畫面。不是實際歷史績效：

- [桌面總覽與比較](images/strategy-lab-desktop.png)
- [明細與來源證據](images/strategy-lab-drilldown.png)
- [390px 窄螢幕](images/strategy-lab-narrow.png)
- [深色](images/strategy-lab-dark.png)
