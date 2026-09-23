# 台股歷史 Universe / Backfill Safety Probe (A1.5)

> **2026-09-23 A5.2 更正**：§3 的產業聯集包含特別股，§4.3 卻把產業聯集全體
> 稱為普通股，兩者矛盾。本輪禁止以碼長／名稱拆分；產業表只能證明股票類成員，
> 不足以單獨驗證 V1 common-stock subtype，舊 stock 宣告在讀取時 fail-closed。
> 2015-01-05 原本未分類的 31 筆已由當日官方 `019919T`（6）與 `9299`（25）
> 驗證為 unsupported。詳見 [A5/A6 正確性契約](taiwan-a5-a6-data-correctness.md)。

> 狀態：**調查報告 + 架構結論。本輪未實作 historical backfill parser，未建立 PIT universe。**
>
> 調查日期：2026-09-22。所有數字均為當日實際 HTTP 回應與本機 Security Master 的實測值。
> 探測全程走 A1 新增的 `app/taiwan/providers/http.py`（namespaced token bucket）。
>
> 目的：在開始 A2「2015 起日 K 大回補」之前，確認目前的 snapshot backfill 是否會因為
> **current** Security Master allowlist，把歷史上存在、今天已下市／下櫃的股票永久丟掉。

---

## 0. 一句話結論

> **會。而且是精確地、只丟掉下市股票。**
>
> 官方原始 snapshot **看得到**歷史上已下市／下櫃的股票；
> `OfficialDailySnapshotAdapter` 用今天的 Security Master allowlist 過濾，把它們全部丟掉。
> 在修正之前開始 A2 大回補，會產生一份**結構性 survivorship-biased** 的 2015–2026 日 K 資料集。

同時發現一個更危險的陷阱：
**TPEx「終止上櫃」不等於下市** —— 抽樣中 2 檔是**轉上市**，今天在 TWSE 活著（見 §5）。

---

## 1. 探測方法

| 步驟 | 做法 |
| --- | --- |
| 樣本來源 | TWSE 終止上市 CSV（`suspendListingCsvAndHtml?type=csv`）、TPEx `company/deListed` |
| 共同歷史日期 | **2024-06-03**（週一，TWSE/TPEx 皆為交易日；10 檔樣本在該日均尚未下市） |
| Raw 官方 snapshot | TWSE `MI_INDEX?date=20240603&type=ALL`、TPEx `dailyQuotes?date=113/06/03` —— **完全不經 Security Master** |
| 目前 adapter | `OfficialDailySnapshotAdapter(security_master=get_security_master()).fetch_date(2024-06-03)` |
| Master 檢查 | `get_security_master().get_instrument(symbol)` |

Security Master 現況：**2,376 檔**；allowlist：TWSE **1,365** / TPEx **1,011**。

---

## 2. 逐檔結果

| symbol | exchange | historical_date | current_master_contains | raw_official_snapshot_contains | current_adapter_emits | conclusion |
| --- | --- | --- | --- | --- | --- | --- |
| 1701 中化 | TWSE | 2024-06-03 | **false** | **true**（收 22.05，量 291,449） | **false** | **DROPPED** — survivorship filtering |
| 2888 新光金 | TWSE | 2024-06-03 | **false** | **true**（收 9.92，量 316,840,402） | **false** | **DROPPED** — survivorship filtering |
| 2809 京城銀 | TWSE | 2024-06-03 | **false** | **true**（收 58.90，量 11,539,753） | **false** | **DROPPED** — survivorship filtering |
| 5371 中強光電 | TPEX | 2024-06-03 | **false** | **true**（收 111.00） | **false** | **DROPPED** — survivorship filtering |
| 4130 健亞 | TPEX | 2024-06-03 | **false** | **true**（收 24.25） | **false** | **DROPPED** — survivorship filtering |
| 3426 台興電子 | TPEX | 2024-06-03 | **false** | **true**（收 42.90） | **false** | **DROPPED** — survivorship filtering |
| 4987 科誠 | TPEX | 2024-06-03 | **false** | **true**（收 61.80） | **false** | **DROPPED** — survivorship filtering |
| 5236 凌陽創新 | TPEX | 2024-06-03 | **false**（TPEX）／**true（TWSE, active）** | **true**（收 189.50） | **false** | **DROPPED**，且 §5 的轉上市陷阱 |
| 2358 廷鑫 | TWSE | 2024-06-03 | false | **false** | false | 非過濾問題 —— 見 §2.1 |
| 2443 昶虹 | TWSE | 2024-06-03 | false | **false** | false | 非過濾問題 —— 見 §2.1 |

下市／下櫃日期（官方）：
1701 `2024-09-02`、2888 `2025-07-24`、2809 `2025-10-01`、6288 `2025-08-15`、6423 `2026-01-22`、
3454 `2026-03-27`、2867 `2026-09-01`；
TPEx：4987 `2026-05-29`、3426 `2026-06-08`、5236 `2026-07-16`、4130 `2026-07-28`、5371 `2026-09-03`。

**達標：TWSE 3 檔（1701 / 2888 / 2809）、TPEx 5 檔（5371 / 4130 / 3426 / 4987 / 5236）
在 raw 官方 snapshot 皆可見，且全部被目前 adapter 丟棄。**

### 2.1 2358 廷鑫 / 2443 昶虹 —— 重要的反例

兩檔在 2024-06-03 的 raw snapshot 中**不存在**。交叉驗證 `STOCK_DAY`：

```
STOCK_DAY 2358 date=20240601 → stat='很抱歉，沒有符合條件的資料!'  rows=0
STOCK_DAY 2443 date=20240601 → stat='很抱歉，沒有符合條件的資料!'  rows=0
STOCK_DAY 2888 date=20240601 → stat='OK'  rows=19  (113/06/03 收 9.92)
```

→ 這兩檔在正式下市日（2024-11-19）之前就已**停止買賣**，snapshot 中沒有它們是**市場事實**，不是端點限制。

**推論警告**：`snapshot 出現` 只能證明「當日有交易」，
**不能**反推 `listing_status`。一檔股票可以「仍上市但停止買賣」。
任何 PIT universe 若用「snapshot 有沒有出現」當 listing membership，會把停牌期誤判成下市。

---

## 3. 量化：allowlist 到底丟掉多少

以 2024-06-03 的官方 TWSE 產業別分表（`MI_INDEX?type=01..33`，見 §4）取得**官方認定的上市普通股**：

| 市場 | 當日官方檔數 | 今天 allowlist 內 | 被丟掉 | 說明 |
| --- | --- | --- | --- | --- |
| TWSE 普通股（4 位純數字） | **962** | 955 | **7** | `1701, 2809, 2867, 2888, 3454, 6288, 6423` |
| TWSE 特別股等（帶字母後綴） | 28 | 0 | 28 | `1101B, 2881A, 2882A...` —— **預期行為**，master 標 `unsupported` |
| TPEx 4 位純數字 | **819** | 806 | **13** | `3202, 3426, 4130, 4945, 4987, 5236, 5371, 6287, 6457, 6514, 6589, 6747, 8420` |

**TWSE 被丟掉的 7 檔，逐一比對官方終止上市 CSV：7/7 全部命中。**
沒有任何一檔是別的原因丟的。過濾行為乾淨到可以直接當作「survivorship filter」的定義。

TPEx 被丟掉的 13 檔中，只有 5 檔在 TPEx 當年度 `deListed` 清單上
（TPEx 該端點只回當年度，見 A1 audit §6.2）；其餘 8 檔無官方歷史清單可比對 —— 這正是
A1 已標記的 TPEx `data_insufficient` 缺口，在這裡具體現形。

---

## 4. Raw snapshot 的 instrument type 可驗證性（決定 A2 能不能安全做）

### 4.1 TWSE —— **官方可驗證，可用**

`MI_INDEX` 的 `type` 參數在**歷史日期**同樣有效，且官方本身就按證券類別分表：

| type | 回傳表 | 2024-06-03 筆數 |
| --- | --- | --- |
| `ALL` | 每日收盤行情(全部) | 39,162（含權證） |
| `ALLBUT0999` | 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證)) | 1,241 |
| `0099P` | 每日收盤行情(**ETF**) | 165 |
| `01`..`31`（31 個產業碼） | 每日收盤行情(水泥工業 / 半導體業 / 金融保險 …) | 合計 **990** |

三檔下市樣本在官方產業分表中的歸屬（實測）：

```
1701 → 每日收盤行情(生技醫療業)
2888 → 每日收盤行情(金融保險)
2809 → 每日收盤行情(金融保險)
2330 → 每日收盤行情(半導體業)      (對照組)
0050 → 不在任何產業表（在 type=0099P ETF 表）  (對照組)
```

→ **TWSE 歷史 instrument type 是官方直接公布的，不需要任何猜測規則。**
> ⚠️ **本節關於「產業別」的部分已於 §4.3 更正作廢** —— 產業標籤不是 point-in-time，
> 且不唯一。只有 `instrument_type` 的結論成立。
`type=01..31` 的聯集 = 當日上市普通股；`type=0099P` = 當日 ETF。

### 4.2 TPEx —— **無法驗證，BLOCKED**

`dailyQuotes` 只有一張 `上櫃股票行情` 表（加一張空的 `管理股票`），且**忽略 `type` 參數**：

```
dailyQuotes?date=113/06/03&type=EW    → 上櫃股票行情 11757 筆
dailyQuotes?date=113/06/03&type=AL    → 上櫃股票行情 11757 筆
dailyQuotes?date=113/06/03&type=RSTA  → 上櫃股票行情 11757 筆
dailyQuotes?date=113/06/03&type=STOCK → 上櫃股票行情 11757 筆
```

11,757 筆的代號長度分佈：

```
6 字元 : 10,931     (權證 + ETF 混在一起，例如 006201、00679B、00687B)
5 字元 :      7
4 字元 :    819
```

表格標題雖然寫「上櫃股票行情」，但 ETF（`006201`）確實在同一張表裡，
**標題不是可信的類型訊號**。

TPEx openapi（225 個端點）中雖有 `tpex_warrant_daily_quts`、`tpex_mainboard_daily_close_quotes`
等分類端點，但都是 **current snapshot 形式、不吃歷史日期參數**，無法對 2015–2026 逐日分類。

→ **要把 TPEx 的 4 位純數字當成「普通股」，只能靠代號格式推測。
這正是「heuristic masquerading as market truth」，本輪依指示不實作。標 `data_insufficient`。**

---

### 4.3 ⚠️ §4.1 的更正（A2 實作期間實測，2026-09-22）

實作 A2b 前重新驗證 `MI_INDEX` 產業分表，發現 §4.1 的結論**有一半是錯的**。
依「發現官方資料與文件矛盾就停止並回報」原則，這裡逐條更正。

#### 實測

```
type=01..40, date=20150105  → 34 個產業碼有資料
type=01..40, date=20240603  → 34 個產業碼有資料（完全相同的 34 個）
```

兩個日期回傳的產業碼集合**完全一樣**，包含
`35 綠能環保`、`36 數位雲端`、`37 運動休閒`、`38 居家生活` ——
這四個 TWSE 產業類別是 **2021 年才設立的**。

```
2015-01-05  type=35 綠能環保 → 3 筆
              8422 可寧衛* 149.50 / 9930 中聯資源 70.10 / 9955 佳龍 18.40
```

#### 結論：membership 是 PIT，label 不是

| 面向 | 實測 | 判定 |
| --- | --- | --- |
| 成員資格（哪些代號出現） | 2015-01-05 產業聯集 855 碼，`ALLBUT0999` 911 碼，**聯集 − ALLBUT0999 = 0** | ✅ **point-in-time**。TWSE 只回當日真的有交易的證券，沒有把未上市公司塞進歷史 |
| 產業標籤 | 2015 的回應用的是**今天**的產業分類（綠能環保等 2021 年才有的類別） | ❌ **不是 point-in-time**，就是「current industry 回貼歷史」 |
| 標籤唯一性 | 2015-01-05 的 855 碼中 **435 碼同時出現在 2 個以上產業表**（例：`1701 → 07 化學生技醫療 + 22 生技醫療業`） | ❌ **產業別不唯一**。07 是被拆成 21/22 的舊傘狀類別，TWSE 新舊都回 |

#### 對 §4.1 的具體更正

§4.1 原文寫「**TWSE 歷史 instrument type 與產業別都是官方直接公布的**」——

- `instrument_type` 部分 **仍然成立**：
  產業表聯集 = 當日普通股、`type=0099P` = 當日 ETF，且成員資格經證實為 PIT。
  A2b 據此產出 `instrument_type`，fail-closed（不在任何產業表 → 不是 verified stock）。
- **產業別部分不成立**：不得宣稱 point-in-time industry。

#### A2b 的處置

```
classification_status = verified   僅適用於 instrument_type
industry              = null
industry_status       = data_insufficient   # 官方僅提供 current 分類，且不唯一
```

A2b **不寫入任何 `industry` 值**，也不把產業表標題當作歷史產業別。
§6.2 設計表格中「industry 來自當日官方產業分表（authoritative，且是 point-in-time 產業別）」
一列**作廢**，以本節為準。

#### 連帶成本更正

§11 決策表假設每個分類日 32 請求（31 產業 + ETF）。
實測有效產業碼為 **34** 個，加 ETF 表 → **每個分類日 35 請求**。
A2b 的實際總量由 A2a census 算出的 `unique_first_seen_dates` 動態決定，不預設。

---

## 5. ⚠️ 轉上市陷阱：「終止上櫃」不等於下市

實測（純本機比對，無 HTTP）：

| 代號 | TPEx `deListed` | 今天在 TWSE allowlist | 今天 master 的 TWSE 紀錄 |
| --- | --- | --- | --- |
| 5236 凌陽創新 | **有**（115-07-16 終止上櫃） | **true** | `('凌陽創新', 'stock', 'active')` |
| 6589 台康生技 | 無（不在當年度清單） | **true** | `('台康生技', 'stock', 'active')` |
| 3426 / 4130 / 4987 / 5371 | 有 | false | None |

**5236 出現在 TPEx 的終止上櫃清單上，但今天在 TWSE 正常交易。**
它不是下市，它是**轉上市（上櫃轉上市）**。

對 PIT universe 的三個直接後果：

1. 把 TPEx「終止上櫃」當成 death event → 會**錯殺**一間還活著的公司，
   並在 backtest 中產生一筆假的「下市歸零／強制出場」。
2. 同一間公司的價格序列會在轉換日斷成 `5236.TPEX` 與 `5236.TWSE` 兩條，
   **canonical symbol 不連續**。目前 repo 沒有任何 symbol continuity／corporate identity 概念。
3. 反向也成立：TWSE 終止上市 CSV 也**沒有原因欄位**（A1 audit §6.1 已記錄），
   無法區分「真下市」與「轉上櫃／合併存續」。

→ **任一方向的「清單出現即死亡」推論都是錯的。** 這是 A2 之後、PIT universe 之前必須解決的問題。

---

## 6. 歷史 backfill mode 架構結論（設計，未實作）

Probe 成立，因此提出設計；但依指示 **本輪不實作 parser**。

### 6.1 不變的部分

`OfficialDailySnapshotAdapter` 目前的 allowlist 行為是 **current product snapshot** 的正確行為：
今天的看板只該顯示今天可交易的標的。**不得更動。**

### 6.2 建議的新路徑（與現有路徑並存，不共用過濾邏輯）

```
historical_market_snapshot=True
```

| 面向 | current product path（現狀，不動） | historical backfill path（新增） |
| --- | --- | --- |
| 來源 | `MI_INDEX?type=ALL` + `dailyQuotes` | TWSE：`MI_INDEX?type=01..31`（股票）＋`type=0099P`（ETF）<br>TPEx：**BLOCKED** |
| 過濾 | current Security Master allowlist | **無 allowlist**；以當日官方分類表的成員資格為準 |
| instrument_type | 來自 current master | 來自**當日官方分表**（authoritative） |
| industry | 來自 current master | 來自**當日官方產業分表**（authoritative，且是 point-in-time 產業別） |
| 未知類型 | 丟棄 | **不硬分類**；寫入時標 `instrument_type=unknown`，不得混入 stock 模型 |

額外好處：TWSE 產業分表同時給出**歷史產業別**，
比「今天的產業別回貼到 2015」正確得多（目前 master 只有今天的產業）。

### 6.3 成本

TWSE 每個交易日需要 32 次請求（31 產業 + 1 ETF），取代目前的 1 次。
2015–2026 約 2,700 個交易日 → 約 **86,400 次請求**。
以 A1 的 `taiwan:twse` 16 rpm 計算約 **90 小時**，不可接受。

替代方案（待決策，本輪不選）：

- **A**：用 `type=ALLBUT0999`（1 次/日，1,241 筆）取全部非權證證券，
  類型留 `unknown`，再用**低頻**（例如每月 1 天）的產業分表建立類型／產業的時間區間表。
  請求量回到約 2,700 + 12×12×32 ≈ 7,300 次。
- **B**：只在有需要時（例如某代號首次出現、或代號集合變動時）才打產業分表。

### 6.4 TPEx 的處置

**BLOCKED。** 在找到官方歷史分類來源之前，不實作 TPEx historical parser。

> **第二輪更新（§9）**：下列三個候選已全部實測並排除，BLOCKED 由「尚未驗證」升級為
> **「已窮盡查證後確認」**。詳見 §9.4。

原始候選清單：
- TPEx 是否有歷史版 `tpex_mainboard_daily_close_quotes`（目前僅 current）；
- 用 TPEx 權證發行基本資料反向扣除；
- MOPS `mopsfin_t187ap03_O`（上櫃股票基本資料）是否有歷史版本。

---

## 7. PIT universe 決策規則（結論，本輪不實作）

Probe 結果對應任務 3 的 **方案 A（部分成立）**：

> TPEx 歷史 `dailyQuotes` **確實會**回傳當時存在、如今已下櫃的股票（5/5 樣本命中）。

因此 **不需要**用 inferred delisting date。但 §4.2 的類型缺口與 §5 的轉上市陷阱，
讓方案 A 只能在 TWSE 側完整成立。最終規則：

```
listing_metadata_status : verified | unknown
    TWSE  → verified   （官方終止上市 CSV，2001-01-20 起，A1 audit §6.1）
    TPEx  → unknown    （官方僅有當年度清單，A1 audit §6.2）
                        且「終止上櫃」語意不等於下市（本文 §5）

tradable_observed       : true | false
    = 該 symbol 當日是否出現在官方 daily snapshot
    這是 observed fact，不是 inference

tradable_source         : official_daily_snapshot

instrument_type_status  : verified | unknown
    TWSE  → verified   （當日官方產業／ETF 分表，本文 §4.1）
    TPEx  → unknown    （本文 §4.2）
```

**關鍵語意分離（來自 §2.1 的反例）**

```
tradable_observed == false   ≠   已下市
                                  （可能只是停止買賣、暫停交易、無成交）
listing_metadata_status       是獨立的一條事實，不可由 tradable_observed 推導
```

**每日 quant backtest universe** 因此定義為：

```
當日 official snapshot 實際出現 (tradable_observed = true)
  AND instrument_type_status = verified
  → 再交 quant_eligibility 做流動性 / warm-up / factor coverage
```

這條定義完全由 observed fact 構成，不含任何 inference。
在 TPEx 類型缺口補上之前，它實際上只覆蓋 TWSE。

**正式績效呈現規則**（沿用任務 3 方案 B 的分離要求，因為 TPEx 仍未 verified）：

```
Primary OOS        : TWSE verified universe（type 可驗證 + 官方下市清單）
Secondary / exp.   : TWSE + TPEx observed universe（type unknown）
```

兩者的 metrics、model artifact、UI 必須分離，
**不得合併宣稱為單一「全市場 OOS」。**

---

## 8. A2 能不能開始？

**不能照現狀開始。**

| 條件 | 狀態 |
| --- | --- |
| Raw 官方 snapshot 看得到歷史下市股 | ✅ 已證實（TWSE 3/3、TPEx 5/5） |
| 目前 adapter 會丟掉它們 | ✅ 已證實（8/8 全丟，TWSE 7 檔非特別股掉落全部命中下市清單） |
| TWSE 歷史 instrument type 可驗證 | ✅ 可用（官方產業／ETF 分表） |
| TPEx 歷史 instrument type 可驗證 | ❌ **BLOCKED**（第二輪已窮盡查證，見 §9.4） |
| 「終止上櫃/上市」語意可信 | ❌ **有轉上市反例（5236、6589）** |
| historical backfill 請求量可接受 | ⚠️ 需先選 §6.3 的 A / B 方案 |

若堅持現在開始 A2，資料集會是 survivorship-biased，
而且**重跑一次也救不回來** —— 因為每日 snapshot 只能用**當日日期**去抓，
今天漏掉的歷史股票，未來只能重抓一次同樣的日期才能補（成本等同重跑全部回補）。

建議順序：

1. 決定 §6.3 的請求量方案（A 或 B）。
2. 實作 TWSE historical backfill path（型別與產業皆 authoritative）。
3. TPEx：要嘛解決 §4.2，要嘛明確接受「TPEx 歷史資料 `instrument_type=unknown`」並隔離績效。
4. 再開始 A2 大回補。

---

## 9. TPEx Historical Instrument-Type Final Probe（A1.5 第二輪）

> 目的：只缺一件事 —— 能否用**官方歷史資料**對 TPEx 當日 row 做 authoritative
> instrument_type 分類。本輪只 probe，未寫 parser。

### 9.1 歷史版 / 可帶日期的 `tpex_mainboard_daily_close_quotes`

```
GET https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
swagger parameters: NONE
```

實測三種寫法，回傳**位元組完全相同**（同一個 md5）：

| 查詢 | rows | payload 內 `Date` | md5 |
| --- | --- | --- | --- |
| 無參數 | 11,596 | `1150922`（今日） | `7be16befea31` |
| `?d=113/06/03` | 11,596 | `1150922` | `7be16befea31` |
| `?date=113/06/03` | 11,596 | `1150922` | `7be16befea31` |

- **不接受歷史 date**：任何日期參數都被忽略，永遠回當日。
- **不能區分類型**：欄位為
  `Date, SecuritiesCompanyCode, CompanyName, Close, Change, Open, High, Low, Average,
  TradingShares, TransactionAmount, TransactionNumber, LatestBidPrice, LatesAskPrice,
  Capitals, NextReferencePrice, NextLimitUp, NextLimitDown`
  —— **沒有任何 instrument_type / 證券類別欄位**。
- 代號長度分佈 `{6: 10696, 5: 8, 4: 892}`，與 `dailyQuotes` 同樣是股票／ETF／權證混在一起。
- **歷史覆蓋：0 天**（只有當日）。回傳的是 **current universe**，不是歷史 universe。
- 附帶穩定性警訊：該端點回傳大 payload 時多次出現
  `httpx.RemoteProtocolError: peer closed connection without sending complete message body`
  與 JSON 截斷，需重試才能取得完整內容。

### 9.2 MOPS `mopsfin_t187ap03_O`（上櫃股票基本資料）

```
GET https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O
rows = 892, Date = 1150921（今日）
```

欄位含 `UnifiedBusinessNo.`、`DateOfListing`、`SecuritiesIndustryCode`、`Symbol`、`IssueShares` 等。

| 檢查項 | 結果 |
| --- | --- |
| 可查歷史日期 / 年度？ | **否**。`?date=20240603`、`?year=113` 與無參數三者 md5 相同（`7b3c1a421694`） |
| 有 stable identifier？ | **有** —— `UnifiedBusinessNo.`（營利事業統一編號） |
| 能表達 market transfer？ | **部分**，見 §10 |
| 只反映 current state？ | **是** |

決定性證據：5 檔已終止上櫃樣本 **全部 ABSENT**
（`5236`、`5371`、`4130`、`4987`、`3426` 皆查無），對照組 `8069 元太` present。
→ 這是**現行登錄名冊**，不保留歷史成員，無法重建任一歷史日期的上櫃 universe。

### 9.3 官方排除集合（ETF / bond / warrant）

構想：`TPEx daily snapshot − official ETF set − official bond set − official warrant set = common stock set`。
成立的前提是**每個排除集合本身都是 point-in-time 的官方資料**。實測：

| 排除集合 | 官方來源 | 可帶歷史日期？ | 結論 |
| --- | --- | --- | --- |
| 權證 | `tpex_warrant_daily_quts`（上櫃權證收盤行情日報表） | **否**。無參數 / `?d=113/06/03` / `?date=113/06/03` 三者 md5 皆為 `7d1884937937`，`Date=1150921`，10,508 筆 | **current-only** |
| ETF | **openapi 225 個端點中沒有任何「上櫃 ETF 清單」**。以 ETF、指數股票、受益憑證、債券、公司債 掃描，只命中國際債券、開放式基金造市商、轉換公司債等 13 個端點，無一是 ETF 成員清單 | — | **不存在** |
| 債券 | `tpex_international_bond_*` / `bond_*` 系列（國際債、寶島債、轉換公司債發行資料） | 未逐一驗證日期參數 | 與上櫃股票 daily snapshot 非同一命名空間，無法直接相減 |

另外以 TPEx `www` 站（就是 `dailyQuotes` / `exDailyQ` / `revivt` 所在、**確實吃歷史日期**的那組）
嘗試 10 條可能路徑，全部失敗：

```
otcEtf 302 / etfQuotes 404 / dailyQuotesEtf 302 / etf 302 / warrantQuotes 302 /
wrDailyQuotes 302 / warrant 302 / dailyWarrant 302 / bondQuotes 302 / otcBond 302
```

→ 排除集合**不是 point-in-time**。拿今天的權證清單回貼 2015 的 snapshot，
正是「current list 回貼歷史」，依指示禁止。

### 9.4 結論：**BLOCKED**

> **TPEx historical instrument_type 找不到 authoritative 官方來源。**

已窮盡的路徑（全部實測，非推測）：

1. `dailyQuotes` —— 吃歷史日期，但只有一張混合表，`type` 參數被忽略（§4.2）。
2. `tpex_mainboard_daily_close_quotes` —— 無類型欄位、不吃日期、current-only。
3. `tpex_mainboard_quotes` / `tpex_exright_daily` —— 無參數、current-only。
4. `mopsfin_t187ap03_O` —— current 登錄名冊，已下櫃者不存在。
5. 官方排除集合 —— 權證 current-only、ETF 清單不存在。
6. TPEx `www` 站的 ETF / 權證 / 債券歷史端點 —— 10 條路徑全 302/404。

**不採用任何 symbol-format heuristic**（「4 位純數字看起來像股票」「6 位像 ETF/權證」）。
TPEx 歷史列一律標：

```
instrument_type        = unknown
instrument_type_status = data_insufficient
```

---

## 10. Market-transfer continuity probe

### 10.1 實測樣本

**TPEx → TWSE（上櫃轉上市）**：取 TWSE `t187ap03_L` 中 `上市日期 >= 20240701` 的 77 檔，
與 2024-06-03 TPEx `dailyQuotes` 成員取交集 → **2 檔**：

| 代號 | 簡稱 | TWSE 上市日 | 統編 | 對照 TPEx 終止上櫃日 |
| --- | --- | --- | --- | --- |
| 6589 | 台康生技 | `20250721` | `54150737` | （2025 年，不在當年度清單窗口） |
| 5236 | 凌陽創新 | `20260716` | `28112364` | **`115-07-16` = 2026-07-16，完全相同** |

**TWSE → TPEx（上市轉上櫃）**：取 TWSE 終止上市 CSV 與現行 TPEx 登錄名冊取交集 → **1 檔**：

| 代號 | 簡稱 | TWSE 終止上市 | TPEx 上櫃日 | 統編 |
| --- | --- | --- | --- | --- |
| **6423** | 億而得 | 民國115年01月22日 = `2026-01-22` | `20260122` | `12894399` |

> **6423 就是 A1 corporate-action 抽樣用過的那一檔（114/06/12 除權，無償配股率 0.0203），
> 也是 A1.5 §3 中被 allowlist 丟掉的 7 檔 TWSE 之一。
> 它沒有死，它在 2026-01-22 從上市轉到上櫃。**
>
> 這證明 §5 的陷阱**雙向成立**：TWSE 終止上市 CSV 同樣不能當作 death event。

**真正的下市/下櫃**：

| 代號 | TWSE 名冊 | TPEx 名冊 | TWSE 終止上市 CSV | 判定 |
| --- | --- | --- | --- | --- |
| 2888 新光金 | ✗ | ✗ | 民國114年07月24日 | **真下市**（證據完整） |
| 1701 中化 | ✗ | ✗ | 民國113年09月02日 | **真下市**（證據完整） |
| 5371 中強光電 | ✗ | ✗ | **無** | **證據只剩 TPEx 當年度清單** |

5371 目前只存在於 TPEx `company/deListed` 的當年度窗口。窗口滾動後，
**官方將不再有任何一處記載它曾經存在** —— 這是 A1 audit §6.2 的 TPEx 缺口最具體的後果。

### 10.2 可建立的 authoritative 證據

| 證據 | 來源 | 品質 |
| --- | --- | --- |
| 穩定公司識別碼 | `營利事業統一編號`（TWSE `t187ap03_L`）／ `UnifiedBusinessNo.`（TPEx `mopsfin_t187ap03_O`） | **verified**，兩交易所同一欄位語意，政府核發，跨市場不變 |
| 轉換日期 | 一側的「終止上市/上櫃日」== 另一側的「上市/上櫃日」 | **verified**，3/3 樣本日期完全相同 |
| 轉換方向 | 由兩側名冊的出現/消失決定 | verified |
| 下市原因 | **無** —— TWSE CSV 無原因欄位；TPEx 有原因但只是法規條號 | `data_insufficient` |

**關鍵限制**：兩個名冊都是 **current-state only**。
一間公司若「先轉市場、後完全下市」，兩邊名冊都不留紀錄，統編也就取不到。
→ **continuity 證據本身也是 survivorship-limited**，只對今天還活著的公司完整。

### 10.3 建議 schema（結論，本輪不實作）

```
canonical_security_id   # 建議用統編；取不到時 fallback 為 "{exchange}:{code}" 並標 unverified
market_symbol           # 5236.TPEX / 5236.TWSE
exchange                # TWSE | TPEX
effective_from          # date
effective_to            # date | null（null = 迄今）
transition_type         # listed | transferred | delisted | unknown
transition_evidence     # verified | inferred | unavailable
```

以 6423 為例，正確的表達是**兩列、一個 canonical_security_id**：

```
{id: 12894399, market_symbol: 6423.TWSE, exchange: TWSE,
 effective_from: <上市日>, effective_to: 2026-01-22,
 transition_type: transferred, transition_evidence: verified}

{id: 12894399, market_symbol: 6423.TPEX, exchange: TPEX,
 effective_from: 2026-01-22, effective_to: null,
 transition_type: listed, transition_evidence: verified}
```

必須達成的兩個目標：

1. `5236.TPEX` 與 `5236.TWSE`（以及 `6423.TWSE` / `6423.TPEX`）**不得被當成兩家公司**。
2. 在 transfer date **不得**強制產生 portfolio liquidation 或 new IPO
   —— 部位應延續，價格序列應接續。

`transition_type = delisted` **只有**在能取得「終止清單 + 對方市場名冊查無」雙重證據時才可標
`verified`；TPEx 側因 §9.4 / A1 audit §6.2 的缺口，預設只能是 `unavailable`。

---

## 11. Request-volume 決策表（§6.3 A/B/C，不自行選擇）

共同前提（實測值）：

- 2015-01-01 → 2026-09-22 約 **2,840** 個交易日。
- A1 節流：`taiwan:twse` / `taiwan:tpex` 各 20 rpm × 0.8 safety = **16 rpm = 3.75 秒/請求**，兩個 bucket 獨立。
- TWSE 每日完整產業掃描 = 31 個產業碼 + 1 個 ETF 碼 = **32 請求**。
- TPEx 每日 = **1 請求**（`dailyQuotes`），**在三個方案中都只能得到 membership，得不到 type**（§9.4）。

| 項目 | **A：每日完整產業掃描** | **B：每日 ALLBUT0999 + 每月掃描** | **C：每日 ALLBUT0999 + 首次出現時掃描** |
| --- | --- | --- | --- |
| 做法 | 每個交易日打 31 產業 + ETF | 每日 1 次 `type=ALLBUT0999` 取非權證全市場；每月挑 1 天做 32 請求掃描，建立「類型/產業的時間區間表」 | 每日 1 次 `ALLBUT0999`；只在出現**尚未分類的新代號**那一天做 32 請求掃描 |
| 官方性 | 100% 官方分類 | 100% 官方分類（區間內插） | 100% 官方分類 |
| TWSE 每日請求 | 32 | 1（+每月 1 天 ×32） | 1（+首見日 ×32） |
| TWSE 總請求 | 2,840 × 32 = **90,880** | 2,840 + 144×32 = **7,448** | 2,840 + 首見日數 × 32，**估 ~19,000**（首見日數未實測，見下） |
| TPEx 總請求 | 2,840 | 2,840 | 2,840 |
| 預估時間（16 rpm，兩 bucket 並行） | TWSE ≈ **94.7 小時** | TWSE ≈ **7.8 小時** | TWSE ≈ **19.8 小時**（估） |
| 保留 PIT industry | ✅ 每日粒度 | ✅ 月粒度（月中變更會延後反映） | ✅ 首見日粒度 |
| 保留 historical delisted stocks | ✅ | ✅ | ✅ |
| 需要 heuristic | ❌ 不需要 | ❌ 不需要 | ❌ 不需要 |
| survivorship risk | 無（TWSE）／TPEx 仍 BLOCKED | 同 A | 同 A |
| classification risk | 無 | **有**：在單一月份內上市又下市/轉出的代號可能從未被任何掃描覆蓋 → 必須標 `unknown`，不得猜 | 低：每個代號在其實際交易日被官方分表覆蓋過 |
| implementation complexity | 最低（無狀態，單一迴圈） | 中（需維護類型/產業區間表 + 未覆蓋代號稽核） | 最高（需追蹤已分類代號集合、觸發式掃描、亂序 resume 較難） |
| 可 resume | ✅ 逐日 partition | ✅ 逐日 partition + 獨立掃描表 | ✅ 但需額外持久化「已分類代號集合」 |
| 推薦用途 | 學術級完整重建；實務上不可接受 | 先跑起來、盡快拿到可用資料集 | 對分類完整性要求最高時 |

**三個方案的共同第一步都是「每日 1 次」的那一趟**（A 也需要逐日迴圈）。
因此建議的決策順序是：

> 先只跑 **每日 1 請求** 的那一趟（TWSE `ALLBUT0999` 2,840 + TPEx `dailyQuotes` 2,840，
> 兩 bucket 並行約 **3 小時**）。
> 跑完就能**實測**出「不同代號集合的首次出現日共有幾天」，
> C 的估值（本表唯一的估計值）就變成實測值，A/B/C 的取捨才是在真實數字上做。

這一趟本身不含任何分類，只記錄 membership（observed fact），
因此**即使之後改選別的方案也不會浪費**。

> ⚠️ 不論選 A、B 或 C，**TPEx 的 `instrument_type` 都是 `unknown`**（§9.4）。
> 三個方案都只解決 TWSE 的分類問題。

---

## 12. 本輪未做 / 待決

第二輪（A1.5 補測）已結案的項目：

1. ~~TPEx 歷史 instrument type 官方來源~~ → **BLOCKED，證據見 §9.4**（6 條路徑全部實測排除）。
2. ~~Symbol continuity / corporate identity~~ → **已取得官方證據與 schema 建議，見 §10**；本輪依指示未實作。

仍未解決：

3. TWSE 終止上市原因 —— CSV 無原因欄位。§10.1 已證明可用「對方市場名冊是否收錄」補上
   `transferred` vs `delisted` 的判別，但**僅對今天還活著的公司有效**（§10.2 限制）。
4. `MI_INDEX` 產業分表在 2015 年是否同樣可用 —— 本輪只驗證 2024-06-03 與當日。
   §11 的「先跑每日 1 請求那一趟」會順便驗證這點。
5. 停止買賣（暫停交易）區間的官方來源 —— 區分「停牌」與「下市」需要它（§2.1 的 2358/2443 反例）。
6. TPEx 債券系列端點（`tpex_international_bond_*` / `bond_*`）的日期參數未逐一驗證；
   即使可帶日期，也無法解決 ETF 清單不存在的問題（§9.3）。
7. §11 表格中 C 方案的「首見日數」為估計值，未實測。
