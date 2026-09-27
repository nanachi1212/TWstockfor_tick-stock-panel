# Historical PIT Evidence Recovery（trend_liquidity_v1）

> 目的：研究並盡可能解除 `trend_liquidity_v1` historical PIT 的兩個全期 blocker
> （`regulatory_history_unavailable`、`tpex_instrument_subtype_blocked`），不降低 strict 規則。
> 只納入可證明 point-in-time 的官方資料；找不到就記錄 unsupported。
> 實測日期：2026-09-27／28。所有請求走 repo 既有的節流 client（`taiwan:twse` / `taiwan:tpex`）。

## 1. 結論

| 項目 | 結論 | 狀態 |
| --- | --- | --- |
| TWSE 處置歷史 | 官方 archive 可回補 2015 起，含公布日期與處置起迄 | **可用，已接入** |
| TPEx 處置歷史 | 官方 archive 可回補 2015 起，含公布日期與處置起訖 | **可用，已接入** |
| TPEx 停止交易（live 用 `tpex_cmode`） | 官方有「依日期」的同一份清單，2015 起 | **可用，已接入** |
| TWSE 終止上市（live 用 `suspendListingCsvAndHtml`） | 既有 `instrument_evidence` 終止上市清單（生效日） | **可用，已接入**（見 §3.4 限制） |
| TPEx 歷史普通股 subtype | 權證／牛熊證可逐日以官方表排除，但下櫃後離開所有名冊的發行人與上櫃轉上市沒有官方 subtype 證據 | **仍 BLOCKED** |

## 2. live v1 的監管排除實際依據

`events_service.check_symbol_risk_status` 在鎖定時讀四份**當期**清單：

| live 來源 | 事件 | 排除條件 |
| --- | --- | --- |
| TWSE OpenAPI `announcement/punish` | disposition | 處置期間涵蓋進場日 |
| TPEx OpenAPI `tpex_disposal_information` | disposition | 同上 |
| TPEx OpenAPI `tpex_cmode` | `SuspensionOfTrading` → suspended | 清單中有該列 |
| TWSE OpenAPI `company/suspendListingCsvAndHtml` | delisting | 生效日 ≤ 進場日，且年度 ≥ 當年 − 2 |

歷史重播必須重建「鎖定當下這四份清單會是什麼」，不能用今天的清單回填。

## 3. 官方歷史來源實測

### 3.1 TWSE 處置 `rwd/zh/announcement/punish?startDate=&endDate=`

- 2015-01、2021-01、2024-06 均 `stat=OK`，欄位固定：`公布日期 / 證券代號 / 處置起迄時間 / 處置措施 …`。
- **查詢語意是「處置期間與區間重疊」**：2021-01 的查詢包含 `109/12/24` 公布、期間跨年的紀錄。
- `total` 與 `data` 筆數一致（用來拒絕截斷回應）。

### 3.2 TPEx 處置 `www/zh-tw/bulletin/disposal?startDate=&endDate=`

- 2015-01、2021-01、2024-06 可查，`date` 回傳查詢區間；同樣是期間重疊語意。
- 無處置的交易日有官方佔位列「本日無處置資料」（無代號）；只略過這種列，其餘缺代號列視為格式錯誤。

### 3.3 TPEx 變更交易／停止交易清單 `web/stock/aftertrading/cmode/chtm_result.php?d=<民國日期>`

- 官方頁面（`zh-tw/mainboard/trading/info/altered.html`）說明可查歷史。
- 2015-01-05、2024-06-03 皆回傳該日清單，欄位與 OpenAPI `tpex_cmode` 相同（含「停止交易」）。
- 非交易日回傳空表且 `stat=ok` → 空表**不接受為證據**。
- 對照：OpenAPI `tpex_spendi_history`（暫停／恢復交易）只含當年度且無公布日期；
  `sprc_result.php?y=` 自 2016 起有資料但無公布日期、語意也不同於 cmode，未採用。

### 3.4 TWSE 終止上市

- 沿用 `instrument_evidence` 既有的終止上市清單（`rwd/zh/company/suspendListing`，2001 起，2026-09-26 取得）。
- 清單只有生效日、沒有公告日：生效日 ≤ 來源日者當時必然已知；
  生效日落在（來源日, 進場日] 且該代號在來源日有成交者，無法證明當時已公告 →
  `regulatory_publication_time_unproven`，不猜測。

## 4. Point-in-time 規則（重播）

來源日 `t`、進場日 `T`（下一個已驗證交易日）；cutoff = 正式鎖定期限（`T` 開盤前）。公布日期為日層級。

- 處置：`公布日期 ≤ t` 且 `處置起 ≤ T ≤ 處置迄`。覆蓋條件：兩個 archive 在 `[T − 120 日, T]` 的所有月份都已取得且無衝突；若任何紀錄的處置期間超過 120 日，建置直接失敗。
- 停止交易：`t` 當日的 cmode 清單中「停止交易」有值。缺 `t` 的清單 → `regulatory_tpex_status_unavailable`。
- 終止上市：`生效日 ≤ t` 且 `生效日 ≥ t 年度 − 2 年的 1/1` → 排除；清單取得日須晚於 `T`。
- 代號同一時間只在一個市場交易，排除以代號對應兩市場的 symbol（與 live 以代號解析的行為一致）。

儲存（`<taiwan_data_root>/regulatory_history/`）：每個官方回應一個 Parquet，footer 保存
`source_url / retrieved_at / raw_sha256`，每列有 `content_hash`。重抓內容不同時保留第一次觀測、寫入
`*.conflict.json`，該分區不再算覆蓋（fail-closed）。
月份分區只涵蓋到「取得日前一天」（上限為月底）；cmode 清單必須在其日期之後取得才算最終版。
月中取得的月份與當日取得的 cmode 會在之後重抓：新回應保留所有舊列才替換並推進涵蓋日，否則記為 conflict。整個 store 的 digest 進入 artifact 的 dataset identity。
不寫入 `user_data` 或正式前瞻批次。

## 5. TPEx historical subtype

### 5.1 新找到的官方逐日來源

`www/zh-tw/afterTrading/otc?date=&type=` 的 `type` **有效**（先前 probe §4.2 只測了 `dailyQuotes`）：

| type | 2024-06-03 | 2015-01-05 | 說明 |
| --- | --- | --- | --- |
| `AL` 全部 | 11,757 | 3,971 | 與 census（`dailyQuotes`）相同 |
| `EW` 不含權證、牛熊證 | 919 | 684 | |
| `WW` 認購售權證 | 10,838 | 3,287 | `AL = EW + WW` 兩天皆完全相等 |
| `EE` ETF | 89 | 1 | |
| `TD` TDR / `80` 管理股票 | 0 / 0 | 1 / 2 | |
| `02`–`38` 產業別 | 820 合計 | — | 只證明「在某產業表」，不證明普通股 |

上櫃 ISIN 名冊（`isin.twse.com.tw/isin/C_public.jsp?strMode=4`）含 `股票 ES` 893、`特別股 EP` 1（`8349A`）、
ETF、ETN、權證等 → 與 TWSE 相同，產業表不足以證明普通股，必須靠 ISIN CFI。

### 5.2 為什麼仍然 BLOCKED

以 `EW` 排除權證後，再以現行 ISIN 名冊（上櫃、上市、未上市未上櫃公開發行）與「上市（櫃）日 ≤ 首次觀測日」檢查，
抽樣 6 個 session 仍有大量**無任何官方型別證據**的上櫃列：

| session | EW ∩ 有價 | 無證據列 | 其中當日成交值 ≥ 5,000 萬 |
| --- | --- | --- | --- |
| 2015-06-02 | 676 | 93 | 10+（如 1795、3658、4130、5466） |
| 2017-03-15 | 721 | 83 | 9 |
| 2019-09-10 | 831 | 87 | 10+（含已下市債券 ETF 00769B） |
| 2021-05-12 | 893 | 82 | 10+（如 5371、6111） |
| 2023-08-08 | 892 | 50 | 9（含上櫃轉上市 5236、6589） |
| 2025-11-18 | 954 | 28 | 6 |

原因都是 survivorship：

1. 下櫃且不再是公開發行公司 → 不在任何現行名冊（例：5371 中強光電，probe §10.1）。
2. 上櫃轉上市 → 現行上市名冊的上市日晚於上櫃期間，現有證據無法證明是同一證券。
3. 已下市的 ETF／ETN 同樣不在現行名冊。

全期 1,083 個曾達 5,000 萬成交值的上櫃代號中，有 128 個屬於上述情況。
依現行 strict 規則（任何有價、無型別證據的觀測列都擋 session），抽樣的 6 個 session 全部仍被擋，
且無證據列多是長期存在的代號（例如 1256、1258、1333 在 2015、2017 兩個樣本都出現）。
逐日抓 `EW`（約 2,859 次請求）預期無法解除 session，且未逐日驗證的日期不能宣稱已解除，
因此本輪**不建立**這條 ingest，blocker 維持原狀；也不使用任何代號格式或名稱推測。

### 5.3 解除所需的後續資料工作

需要能涵蓋「已離開所有名冊的上櫃發行人」與「轉市場連續性」的官方歷史來源
（例如上櫃歷史終止清單的完整 archive，或可按日期查詢的上櫃證券型別名冊）。本輪未找到。
