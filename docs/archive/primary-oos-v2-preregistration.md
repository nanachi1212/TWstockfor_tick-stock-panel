# Primary OOS V2 預登記

## 目的與證據邊界

本預登記先固定候選、選擇規則、confirmatory window 與成功條件，再由未來資料回答候選是否值得進入下一階段。它不用來找出 2018–2026 期間回測最好的權重。

2018-10-24 至 2026-08-04 已用於正式 V1 評估與後續 diagnostics。後續任何在這段期間的 candidate comparison、nested validation、ablation、direction study 或 weighting study 都必須標記為 `retrospective_diagnostic`。不得把這些結果標記為 confirmatory、untouched、clean OOS 或 prospective。

Machine-readable 契約位於 `backend/app/taiwan/quant/primary_v2_preregistration.py`。目前內容的 `preregistration_id` 是：

```text
f089df5356afc00d9230f9b6db7c4f47c58f118150c8370913904b906bd43c82
```

任何候選契約、selection rule、confirmatory policy、benchmark、success criteria、`created_at` 或版本變更都會產生新的 `preregistration_id`。舊預登記不得被重新解釋或覆寫。

## 凍結 V1

- Run ID: `2538dbd6-acc5-43ae-9061-a74149aeb9f1`
- Dataset identity: `c0336dd5980a165bfb0fa1fa99b7e5438ea4bd862ade90e17b73210296e57626`
- Evaluation period: 2018-10-24 至 2026-08-04
- Candidate key: `primary_v1_equal_momentum`
- Model identity: `df729c318dfd1cde31405527f296317ac4204219eddf8f15508facdeb6d11b0f`
- Factors: `momentum_5d`、`momentum_20d`、`momentum_60d`
- Direction: 全部 `higher_is_better`
- Weights: 固定等權

Model identity 是 canonical model contract 的 SHA-256。Contract 包含 factor set、direction、weight、normalization、missing policy、universe policy、PIT policy、scorer version、label horizons、bucket rule 與 benchmark。Display name 相同但 contract 不同的候選會有不同 identity。

V1 artifact 與 diagnostics artifact 都是 historical evidence。本工作包不修改 production scorer、factor panel 或舊 artifact。

## 預登記候選

三個候選的狀態都是 `development_candidates`：

1. `primary_v1_equal_momentum`：`momentum_5d`、`momentum_20d`、`momentum_60d` 等權；identity `df729c318dfd1cde31405527f296317ac4204219eddf8f15508facdeb6d11b0f`。
2. `primary_v2_momentum_60d`：只使用 `momentum_60d`；identity `62f1569f75b96c37aec9414bcd8db29283865a8e9beb98499988e895a02339e1`。
3. `primary_v2_momentum_20d_60d`：`momentum_20d` 與 `momentum_60d` 等權；identity `50ee6861885189ed9e807e478fcf14af93b2d292a8f524018f7a5f40e745081c`。

本預登記不允許第四個候選、連續權重搜尋、factor subset 最佳化、自動 factor search，也不允許反轉 `momentum_5d`。

## Deterministic nested selection

Outer test fold 不得進入 candidate selection。Selector 只可使用 outer train 與 inner validation，並依下列順序選擇：

1. 先排除少於 40 個 valid dates、1,200 筆 observations，或任一日有效橫斷面少於 30 筆的候選。
2. 排除 primary metric 或 secondary metric 不可用的候選。不得把不可用證據補成 0。
3. Primary metric 為 `direction_adjusted_mean_ic`，數值較高者優先。
4. Secondary metric 為 `top_minus_universe`，數值較高者優先。
5. 數值完全相同時，依 A、B、C 的預登記順序決定。

Selector 是 deterministic helper。AI 不參與選擇。不得根據 outer test 結果回頭修改規則。現有 Primary OOS runner 還沒有啟用這個 nested selector。任何 retrospective runner 只能產生 diagnostic evidence。

## Untouched confirmatory window

Confirmatory start 不是用自然日或平日推定。程式只接受正式 TWSE session evidence，並選取 2026-08-04 後第一個 verified trading session。從起點開始的前 63 個 verified sessions 是 decision block。

Status 只有：

- `waiting_for_sessions`：decision sessions 少於 63。
- `waiting_for_labels`：63 個 sessions 已完成，但最後一個 decision session 的 20D label 還沒有完整未來區間。
- `ready`：63 個 sessions 已完成，且最後一個 decision session 的 20D label 已成熟。
- `evaluated`：ready 後已產生一次正式 confirmatory run。

5D 與 20D maturity 分開記錄。Pending label 不得被刪除後提前評估，也不得補 0、使用 partial future return，或用目前收盤價代替。

## Leakage guard

`EvaluationPurpose` 只有 `retrospective_diagnostic`、`development_selection` 與 `confirmatory_evaluation`。中央 `guard_evaluation_frame` 套用下列規則：

- Development 與 retrospective reads 會排除 confirmatory start 當日及之後的 rows。
- Confirmatory reads 只能在 status 為 `ready` 時執行。
- Confirmatory reads 只會返回預登記的 63-session decision block。

目前 framework 已完成 schema、readiness、selector 與 guard，但不會在 block 成熟前建立 confirmatory runner。`ConfirmatoryRunContract` 會把 preregistration ID、candidate identity、selection rule identity、confirmatory 起訖、dataset identity 與 horizons 固定為 deterministic run identity；status 未達 `ready` 時會 fail closed。

## 事先定義的成功條件

5D 與 20D 分開評估。每個 horizon 必須報告 mean IC、median IC、positive IC ratio、top bucket return、universe return、bottom bucket return、top-minus-universe、long-short、top-minus-0050 與 valid date count。

有效日期少於 50，或任一核心指標不可用時，結果是 `insufficient_evidence`。其他情況需同時滿足：

1. Direction-adjusted mean IC > 0。
2. Top-minus-universe > 0。
3. Long-short > 0。
4. Top-minus-0050 >= 0。
5. 沒有重大 PIT 或 data-quality violation。

本預登記不要求每日單調，也不發明統計顯著性門檻。

## Benchmark 契約

新 framework 提供 optional benchmark fields：`benchmark_symbol`、`benchmark_return`、`top_minus_benchmark` 與 `valid_benchmark_dates`。舊 artifact 沒有這些欄位時仍可讀取。Benchmark 不可用時，return 與 excess return 必須是 `null`，不得補 0。本工作包不 migration 舊 artifact。

## 本工作包不包含

- LightGBM、XGBoost、neural network、Bayesian optimizer 或 Optuna。
- Exhaustive factor search 或全期間最佳權重搜尋。
- 修改 production V1 scorer、factor panel 或舊 OOS artifact。
- 根據 confirmatory 中途結果更換候選。

LightGBM 狀態固定為 `NOT_YET`。
