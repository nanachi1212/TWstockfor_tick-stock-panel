"""enriched 表計算流水線(§7.5 / §7.7 Step 2)。

儲存層 (enriched parquet):
  僅儲存基礎行情窄表 (14 欄), 指標和信號由各服務即時計算。

  儲存欄: symbol, date, OHLCV(前復權), volume, amount,
          raw_close, raw_high, raw_low, turnover_rate,
          consecutive_limit_ups, consecutive_limit_downs

設計:
  - 100% Polars 表達式(SQL 視窗無法表達遞迴 EMA)
  - 每隻標的獨立計算(`.over("symbol")`)
  - 有 adj_factor 時先應用前復權再算指標;無因子時直接用 raw
"""
from __future__ import annotations

import logging

import polars as pl

from app.price_limits import (
    polars_limit_price,
    polars_price_limit_pct,
    taiwan_limit_profiles,
)
from app.share_capital import apply_historical_float_shares
from app.volume_units import turnover_rate_pct_expr

logger = logging.getLogger(__name__)


# enriched parquet 僅儲存的列 (14 欄)
ENRICHED_STORAGE_COLS = [
    "symbol", "date",
    "open", "high", "low", "close",          # 前復權
    "volume", "amount",
    "raw_close", "raw_high", "raw_low",       # 不復權原始價
    "turnover_rate",                           # 依賴當時的 float_shares, 不可回推
    "consecutive_limit_ups",                   # 遞推狀態, 需從歷史 cum_sum
    "consecutive_limit_downs",
    "quote_ts",                                # 行情時間戳(ms): 盤後校驗/量比折算/跨天完整性
]


# ================================================================
# enriched 完整列清單 (儲存 + 執行時計算)
# 供 AI 審查代碼時參考: 策略/篩選/回測 可直接使用以下欄名。
# 分類: 儲存欄 → 指標欄 → 信號欄 → JOIN 欄
# ================================================================
ENRICHED_COLUMNS: dict[str, dict[str, str]] = {
    # ── 儲存欄 (parquet 持久化) ──────────────────────────
    "symbol":                  "股票代號",
    "date":                    "交易日期",
    "open":                    "前復權開盤價",
    "high":                    "前復權最高價",
    "low":                     "前復權最低價",
    "close":                   "前復權收盤價",
    "volume":                  "成交量",
    "amount":                  "成交額",
    "raw_close":               "原始收盤價(未復權)",
    "raw_high":                "原始最高價(未復權)",
    "raw_low":                 "原始最低價(未復權)",
    "turnover_rate":           "週轉率",
    "consecutive_limit_ups":   "連板數",
    "consecutive_limit_downs": "連跌數",
    # ── 基礎指標 ─────────────────────────────────────────
    "prev_close":              "前收盤價",
    "change_pct":              "日漲跌幅(小數, 如 0.05 = 5%)",
    "change_amount":           "日漲跌額",
    "amplitude":               "日振幅 (最高-最低)/昨收",
    # ── 均線 MA ──────────────────────────────────────────
    "ma5":                     "5日簡單均線",
    "ma10":                    "10日簡單均線",
    "ma20":                    "20日簡單均線",
    "ma30":                    "30日簡單均線",
    "ma60":                    "60日簡單均線(季線)",
    # ── 指數均線 EMA ─────────────────────────────────────
    "ema5":                    "5日指數均線",
    "ema10":                   "10日指數均線",
    "ema20":                   "20日指數均線",
    "ema30":                   "30日指數均線",
    "ema60":                   "60日指數均線",
    # ── MACD ─────────────────────────────────────────────
    "macd_dif":                "MACD DIF線(快線-慢線)",
    "macd_dea":                "MACD DEA線(信號線)",
    "macd_hist":               "MACD柱狀圖 (DIF-DEA)×2",
    # ── 布林帶 BOLL ──────────────────────────────────────
    "boll_upper":              "布林帶上軌 MA20+2σ",
    "boll_lower":              "布林帶下軌 MA20-2σ",
    # ── KDJ ──────────────────────────────────────────────
    "kdj_k":                   "KDJ K值",
    "kdj_d":                   "KDJ D值",
    "kdj_j":                   "KDJ J值 (3K-2D)",
    # ── ATR ──────────────────────────────────────────────
    "atr_14":                  "14日平均真實波幅",
    # ── 量價 ─────────────────────────────────────────────
    "vol_ma5":                 "5日成交均量",
    "vol_ma10":                "10日成交均量",
    "vol_ratio_5d":            "量比 (成交量/5日均量)",
    # ── 极值 ─────────────────────────────────────────────
    "high_60d":                "60日最高價",
    "low_60d":                 "60日最低價",
    # ── 動量 ─────────────────────────────────────────────
    "momentum_5d":             "5日動量(漲跌幅小數)",
    "momentum_10d":            "10日動量",
    "momentum_20d":            "20日動量",
    "momentum_30d":            "30日動量",
    "momentum_60d":            "60日動量",
    # ── 波動率 ───────────────────────────────────────────
    "annual_vol_20d":          "20日年化波動率",
    # ── RSI ──────────────────────────────────────────────
    "rsi_6":                   "6日相對強弱指標",
    "rsi_14":                  "14日相對強弱指標",
    "rsi_24":                  "24日相對強弱指標",
    # ── 信號欄 (bool) ────────────────────────────────────
    "signal_ma_golden_5_20":   "MA5上穿MA20 (金叉)",
    "signal_ma_dead_5_20":     "MA5下穿MA20 (死叉)",
    "signal_ma_golden_20_60":  "MA20上穿MA60",
    "signal_macd_golden":      "MACD金叉 (DIF上穿DEA)",
    "signal_macd_dead":        "MACD死叉 (DIF下穿DEA)",
    "signal_ma20_breakout":    "收盤突破MA20上方",
    "signal_ma20_breakdown":   "收盤跌破MA20下方",
    "signal_ma5_breakout":     "收盤突破MA5上方",
    "signal_ma5_breakdown":    "收盤跌破MA5下方",
    "signal_ma10_breakout":    "收盤突破MA10上方",
    "signal_ma10_breakdown":   "收盤跌破MA10下方",
    "signal_n_day_high":       "創60日新高",
    "signal_n_day_low":        "創60日新低",
    "signal_boll_breakout_upper": "突破布林上軌",
    "signal_boll_breakdown_lower": "跌破布林下軌",
    "signal_volume_surge":     "放量 (量比≥2.0)",
    "signal_limit_up":         "漲停",
    "signal_limit_down":       "跌停",
    "signal_limit_down_recovery": "跌停翹板(跌停後回升)",
    "signal_broken_limit_up":  "炸板(最高觸及漲停但收盤未封住)",
    # ── JOIN 欄 (由 repository 從 instruments 表補充) ───
    "name":                    "股票名稱 (來自 instruments)",
    "total_shares":            "總股本 (來自 instruments)",
    "float_shares":            "流通在外股數本 (來自 instruments)",
}

# 僅供 AI/開發者快速索引: 按類別的欄名列表
ENRICHED_COLUMNS_BY_CATEGORY: dict[str, list[str]] = {
    "storage":  [k for k in ENRICHED_COLUMNS if k in ENRICHED_STORAGE_COLS],
    "basic":    ["prev_close", "change_pct", "change_amount", "amplitude"],
    "ma":       ["ma5", "ma10", "ma20", "ma30", "ma60"],
    "ema":      ["ema5", "ema10", "ema20", "ema30", "ema60"],
    "macd":     ["macd_dif", "macd_dea", "macd_hist"],
    "boll":     ["boll_upper", "boll_lower"],
    "kdj":      ["kdj_k", "kdj_d", "kdj_j"],
    "atr":      ["atr_14"],
    "volume":   ["vol_ma5", "vol_ma10", "vol_ratio_5d"],
    "extremes": ["high_60d", "low_60d"],
    "momentum": ["momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d"],
    "volatility": ["annual_vol_20d"],
    "rsi":      ["rsi_6", "rsi_14", "rsi_24"],
    "signals":  [k for k in ENRICHED_COLUMNS if k.startswith("signal_")],
    "join":     ["name", "total_shares", "float_shares"],
}


def _ema_alpha(span: int) -> float:
    return 2.0 / (span + 1)


def _apply_adj_factor(raw: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    """對 raw K 線應用前復權 (forward adjustment)。

    adj_factor 結構: symbol, trade_date, ex_factor
    ex_factor 含義: 每次除權事件的 pre/post 比值(個股級,非累積)。

    前復權原理:
      - 保持最新價格不變,將歷史價格向下調整以消除除權缺口
      - adjusted = raw × cumprod_at_D / total_cumprod
      - 等價於: adjusted = raw / (該日期之後所有事件的 ex_factor 乘積)
    """
    if factors.is_empty():
        return raw

    # 確保類型一致
    factors = factors.with_columns(
        pl.col("trade_date").cast(pl.Date, strict=False),
        pl.col("ex_factor").cast(pl.Float64, strict=False),
    ).select("symbol", "trade_date", "ex_factor").drop_nulls()

    if factors.is_empty():
        return raw

    # 去重 + 排序 + 累積乘積 (一趟完成)
    factors_sorted = (
        factors.sort(["symbol", "trade_date"])
        .unique(subset=["symbol", "trade_date"])
        .sort(["symbol", "trade_date"])
        .with_columns(
            pl.col("ex_factor").cum_prod().over("symbol").alias("cum_factor"),
        )
    )

    # 每個 symbol 的總累積因子
    total_factors = (
        factors_sorted
        .group_by("symbol")
        .agg(pl.col("cum_factor").last().alias("total_factor"))
    )

    raw_sorted = raw.sort(["symbol", "date"])

    # join_asof backward: 每根 K 線取 <= 其 date 的最新累積因子
    # 同時帶 trade_date 欄用於判斷除權日標記
    df = raw_sorted.join_asof(
        factors_sorted.select("symbol", "trade_date", "cum_factor"),
        left_on="date",
        right_on="trade_date",
        by="symbol",
        strategy="backward",
    )

    # 補充 total_factor + 前復權 + 除權標記,一次 with_columns 完成
    df = df.join(total_factors, on="symbol", how="left")

    is_ex = pl.col("trade_date") == pl.col("date")
    ratio = pl.col("cum_factor").fill_null(1.0) / pl.col("total_factor").fill_null(1.0)
    price_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]

    df = df.with_columns(
        [pl.col(c) * ratio for c in price_cols]
        + [
            is_ex.alias("ex_rights"),
        ]
    ).drop(["trade_date", "cum_factor", "total_factor"])

    return df


# ================================================================
# 技術指標計算 (從 OHLCV 計算)
# ================================================================

# ── compute_indicators 的列依賴關係 (供 needed 裁剪時求閉包) ────────────
# target -> 其計算所依賴的中間/指標欄 (僅列出依賴非原始 OHLCV 的項)
_INDICATOR_DEPS: dict[str, set[str]] = {
    "macd_dif": {"_ema12", "_ema26"},
    "boll_upper": {"ma20", "_boll_std"},
    "boll_lower": {"ma20", "_boll_std"},
    "macd_dea": {"macd_dif"},
    "macd_hist": {"macd_dif", "macd_dea"},
    "kdj_k": {"_kdj_ln", "_kdj_hn"},
    "kdj_d": {"kdj_k"},
    "kdj_j": {"kdj_k", "kdj_d"},
    "atr_14": {"_tr"},
    "vol_ratio_5d": {"_vol_ma5"},
    "annual_vol_20d": {"_daily_pct"},
    "rsi_6": {"_delta", "_gain", "_loss"},
    "rsi_14": {"_delta", "_gain", "_loss"},
    "rsi_24": {"_delta", "_gain", "_loss"},
}

# compute_indicators 可產出的全部指標/臨時欄 (needed=None 時即為此全集, 行為不變)
_ALL_INDICATOR_COLS: frozenset[str] = frozenset({
    "prev_close", "ma5", "ma10", "ma20", "ma30", "ma60",
    "ema5", "ema10", "ema20", "ema30", "ema60", "_ema12", "_ema26",
    "_boll_std", "_kdj_ln", "_kdj_hn", "_tr", "vol_ma5", "vol_ma10",
    "_vol_ma5", "high_60d", "low_60d",
    "macd_dif", "boll_upper", "boll_lower", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "atr_14", "vol_ratio_5d",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
    "change_pct", "change_amount", "amplitude", "_daily_pct", "annual_vol_20d",
    "rsi_6", "rsi_14", "rsi_24",
})


def _resolve_needed(needed: set[str] | None) -> set[str]:
    """把 needed 展開為閉包 (含所依賴的中間欄)。needed=None → 全集。"""
    if needed is None:
        return set(_ALL_INDICATOR_COLS)
    want = set(needed)
    changed = True
    while changed:
        changed = False
        for target in list(want):
            deps = _INDICATOR_DEPS.get(target)
            if deps and not deps <= want:
                want |= deps
                changed = True
    return want


def compute_indicators(
    df: pl.DataFrame,
    needed: set[str] | None = None,
    *,
    assume_sorted: bool = False,
) -> pl.DataFrame:
    """從 OHLCV 資料計算全套技術指標。

    輸入必須包含: symbol, date, open, high, low, close, volume
    回傳添加了所有指標欄的 DataFrame。

    needed:
        None (預設) — 計算全部指標, 行為與歷史逐位一致 (所有 gate 為真, 表達式/順序不變)。
        欄名集合   — 僅計算這些列及其依賴閉包, 跳過無關的 EMA/KDJ/RSI 等 pass;
                     輸出保留輸入欄 + 所需指標欄。被保留欄的數值與全量計算逐位一致
                     (逐欄 window/rolling 相互獨立, 跳過其它列不影響保留欄)。
    """
    if df.is_empty():
        return df

    import time as _time
    _t0 = _time.perf_counter()

    want = _resolve_needed(needed)

    df = df if assume_sorted else df.sort(["symbol", "date"])

    # Pass 1: 均線 + EMA + MACD 基礎 + BOLL 基礎 + KDJ 基礎 + ATR 基礎 + 量價 + 極值
    prev_close = pl.col("close").shift(1).over("symbol")
    _p1: list[pl.Expr] = []
    if "prev_close" in want:
        _p1.append(prev_close.alias("prev_close"))
    if "ma5" in want:
        _p1.append(pl.col("close").rolling_mean(5).over("symbol").alias("ma5"))
    if "ma10" in want:
        _p1.append(pl.col("close").rolling_mean(10).over("symbol").alias("ma10"))
    if "ma20" in want:
        _p1.append(pl.col("close").rolling_mean(20).over("symbol").alias("ma20"))
    if "ma30" in want:
        _p1.append(pl.col("close").rolling_mean(30).over("symbol").alias("ma30"))
    if "ma60" in want:
        _p1.append(pl.col("close").rolling_mean(60).over("symbol").alias("ma60"))
    if "ema5" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(5), adjust=False).over("symbol").alias("ema5"))
    if "ema10" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(10), adjust=False).over("symbol").alias("ema10"))
    if "ema20" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(20), adjust=False).over("symbol").alias("ema20"))
    if "ema30" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(30), adjust=False).over("symbol").alias("ema30"))
    if "ema60" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(60), adjust=False).over("symbol").alias("ema60"))
    if "_ema12" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(12), adjust=False).over("symbol").alias("_ema12"))
    if "_ema26" in want:
        _p1.append(pl.col("close").ewm_mean(alpha=_ema_alpha(26), adjust=False).over("symbol").alias("_ema26"))
    if "_boll_std" in want:
        _p1.append(pl.col("close").rolling_std(20).over("symbol").alias("_boll_std"))
    if "_kdj_ln" in want:
        _p1.append(pl.col("low").rolling_min(9).over("symbol").alias("_kdj_ln"))
    if "_kdj_hn" in want:
        _p1.append(pl.col("high").rolling_max(9).over("symbol").alias("_kdj_hn"))
    if "_tr" in want:
        _p1.append(pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        ).alias("_tr"))
    if "vol_ma5" in want:
        _p1.append(pl.col("volume").rolling_mean(5).over("symbol").alias("vol_ma5"))
    if "vol_ma10" in want:
        _p1.append(pl.col("volume").rolling_mean(10).over("symbol").alias("vol_ma10"))
    if "_vol_ma5" in want:
        _p1.append(pl.col("volume").rolling_mean(5).over("symbol").alias("_vol_ma5"))
    if "vol_ratio_5d" in want:
        # 前5日平均成交量(不含當天), 標準量比分母: volume.shift(1).rolling_mean(5)
        _p1.append(pl.col("volume").shift(1).rolling_mean(5).over("symbol").alias("_vol_ma5_prev"))
    if "high_60d" in want:
        _p1.append(pl.col("close").rolling_max(60).over("symbol").alias("high_60d"))
    if "low_60d" in want:
        _p1.append(pl.col("close").rolling_min(60).over("symbol").alias("low_60d"))
    if _p1:
        df = df.with_columns(_p1)

    # Pass 2: MACD + BOLL (基於 Pass 1 基礎欄)
    _p2: list[pl.Expr] = []
    if "macd_dif" in want:
        _p2.append((pl.col("_ema12") - pl.col("_ema26")).alias("macd_dif"))
    if "boll_upper" in want:
        _p2.append((pl.col("ma20") + 2 * pl.col("_boll_std")).alias("boll_upper"))
    if "boll_lower" in want:
        _p2.append((pl.col("ma20") - 2 * pl.col("_boll_std")).alias("boll_lower"))
    if _p2:
        df = df.with_columns(_p2)
    if "macd_dea" in want:
        df = df.with_columns(
            pl.col("macd_dif").ewm_mean(alpha=_ema_alpha(9), adjust=False).over("symbol").alias("macd_dea"),
        )
    if "macd_hist" in want:
        df = df.with_columns(
            ((pl.col("macd_dif") - pl.col("macd_dea")) * 2).alias("macd_hist"),
        )

    # Pass 3: KDJ
    if "kdj_k" in want:
        _kdj_rsv = (
            100 * (pl.col("close") - pl.col("_kdj_ln"))
            / (pl.col("_kdj_hn") - pl.col("_kdj_ln")).fill_null(1e-12)
        )
        df = df.with_columns([
            _kdj_rsv.ewm_mean(alpha=1.0 / 3, adjust=False).over("symbol").alias("kdj_k"),
        ])
    if "kdj_d" in want:
        df = df.with_columns([
            pl.col("kdj_k").ewm_mean(alpha=1.0 / 3, adjust=False).over("symbol").alias("kdj_d"),
        ])
    if "kdj_j" in want:
        df = df.with_columns([
            (3 * pl.col("kdj_k") - 2 * pl.col("kdj_d")).alias("kdj_j"),
        ])

    # Pass 4: ATR + 量比 + 動量 + 波動 + 漲跌幅 + 漲跌額 + 振幅
    if "atr_14" in want:
        df = df.with_columns(
            pl.col("_tr").ewm_mean(alpha=1.0 / 14, adjust=False).over("symbol").alias("atr_14"),
        )
    if "vol_ratio_5d" in want:
        # 標準量比: 今日成交量 / 前5日均量(不含當天)
        # 盤後全量路徑: 當日 volume 是完整全天量, 無需時間折算
        df = df.with_columns(
            (pl.col("volume") / pl.col("_vol_ma5_prev")).alias("vol_ratio_5d"),
        )
    _p4mom: list[pl.Expr] = []
    if "momentum_5d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("momentum_5d"))
    if "momentum_10d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(10).over("symbol") - 1).alias("momentum_10d"))
    if "momentum_20d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(20).over("symbol") - 1).alias("momentum_20d"))
    if "momentum_30d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(30).over("symbol") - 1).alias("momentum_30d"))
    if "momentum_60d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(60).over("symbol") - 1).alias("momentum_60d"))
    if "change_pct" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("change_pct"))
    if _p4mom:
        df = df.with_columns(_p4mom)
    if "change_amount" in want:
        df = df.with_columns(
            (pl.col("close") - pl.col("close").shift(1).over("symbol")).alias("change_amount"),
        )
    if "amplitude" in want:
        df = df.with_columns(
            pl.when(pl.col("close").shift(1).over("symbol") > 0)
              .then((pl.col("high") - pl.col("low")) / pl.col("close").shift(1).over("symbol"))
              .otherwise(None)
              .alias("amplitude"),
        )
    if "_daily_pct" in want:
        df = df.with_columns(
            pl.col("close").pct_change().over("symbol").alias("_daily_pct"),
        )
    if "annual_vol_20d" in want:
        df = df.with_columns(
            (pl.col("_daily_pct").rolling_std(20).over("symbol") * (252 ** 0.5))
                .alias("annual_vol_20d"),
        )

    # Pass 5: RSI
    if want & {"rsi_6", "rsi_14", "rsi_24"}:
        df = df.with_columns(
            pl.col("close").diff().over("symbol").alias("_delta"),
        ).with_columns([
            pl.when(pl.col("_delta") > 0).then(pl.col("_delta")).otherwise(0.0).alias("_gain"),
            pl.when(pl.col("_delta") < 0).then(-pl.col("_delta")).otherwise(0.0).alias("_loss"),
        ])
        for n in (6, 14, 24):
            if f"rsi_{n}" not in want:
                continue
            a = 1.0 / n
            df = df.with_columns([
                pl.col("_gain").ewm_mean(alpha=a, adjust=False).over("symbol").alias(f"_rsi_avg_gain_{n}"),
                pl.col("_loss").ewm_mean(alpha=a, adjust=False).over("symbol").alias(f"_rsi_avg_loss_{n}"),
            ]).with_columns(
                (100 - 100 / (1 + pl.col(f"_rsi_avg_gain_{n}") /
                             pl.when(pl.col(f"_rsi_avg_loss_{n}") == 0)
                               .then(1e-12)
                               .otherwise(pl.col(f"_rsi_avg_loss_{n}"))
                             )).alias(f"rsi_{n}"),
            )

    # Pass 6: 週轉率 (需要 float_shares, 後續在 compute_all 中 JOIN instruments 後補充)

    # 清理臨時欄 (只丟棄實際存在的臨時欄)
    _temp_cols = ["_boll_std", "_tr", "_ema12", "_ema26",
                  "_kdj_ln", "_kdj_hn", "_vol_ma5", "_vol_ma5_prev", "_daily_pct",
                  "_delta", "_gain", "_loss",
                  "_rsi_avg_gain_6", "_rsi_avg_loss_6",
                  "_rsi_avg_gain_14", "_rsi_avg_loss_14",
                  "_rsi_avg_gain_24", "_rsi_avg_loss_24"]
    df = df.drop([c for c in _temp_cols if c in df.columns])

    _elapsed = (_time.perf_counter() - _t0) * 1000
    import logging as _logging
    _logging.getLogger(__name__).debug("compute_indicators: %.1fms, %d rows", _elapsed, len(df))

    return df


SIGNAL_DEPENDENCIES: dict[str, frozenset[str]] = {
    "signal_ma_golden_5_20": frozenset({"ma5", "ma20"}),
    "signal_ma_dead_5_20": frozenset({"ma5", "ma20"}),
    "signal_ma_golden_20_60": frozenset({"ma20", "ma60"}),
    "signal_macd_golden": frozenset({"macd_dif", "macd_dea"}),
    "signal_macd_dead": frozenset({"macd_dif", "macd_dea"}),
    "signal_ma20_breakout": frozenset({"close", "ma20"}),
    "signal_ma20_breakdown": frozenset({"close", "ma20"}),
    "signal_ma5_breakout": frozenset({"close", "ma5"}),
    "signal_ma5_breakdown": frozenset({"close", "ma5"}),
    "signal_ma10_breakout": frozenset({"close", "ma10"}),
    "signal_ma10_breakdown": frozenset({"close", "ma10"}),
    "signal_n_day_high": frozenset({"close", "high_60d"}),
    "signal_n_day_low": frozenset({"close", "low_60d"}),
    "signal_boll_breakout_upper": frozenset({"close", "boll_upper"}),
    "signal_boll_breakdown_lower": frozenset({"close", "boll_lower"}),
    "signal_volume_surge": frozenset({"vol_ratio_5d"}),
}

LIMIT_SIGNAL_OUTPUTS: frozenset[str] = frozenset({
    "signal_limit_up",
    "signal_limit_down",
    "signal_limit_down_recovery",
    "signal_broken_limit_up",
    "consecutive_limit_ups",
    "consecutive_limit_downs",
    "turnover_rate",
})

def compute_signals(df: pl.DataFrame, needed: set[str] | None = None) -> pl.DataFrame:
    """從已有指標欄計算原子信號布林列。

    輸入必須包含 compute_indicators() 產出的指標欄。
    """
    if df.is_empty():
        return df

    want = set(SIGNAL_DEPENDENCIES) if needed is None else set(needed) & set(SIGNAL_DEPENDENCIES)
    expressions: dict[str, pl.Expr] = {
        "signal_ma_golden_5_20": ((pl.col("ma5") > pl.col("ma20")) &
         (pl.col("ma5").shift(1).over("symbol") <= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma_golden_5_20"),
        "signal_ma_dead_5_20": ((pl.col("ma5") < pl.col("ma20")) &
         (pl.col("ma5").shift(1).over("symbol") >= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma_dead_5_20"),
        "signal_ma_golden_20_60": ((pl.col("ma20") > pl.col("ma60")) &
         (pl.col("ma20").shift(1).over("symbol") <= pl.col("ma60").shift(1).over("symbol")))
            .alias("signal_ma_golden_20_60"),
        "signal_macd_golden": ((pl.col("macd_dif") > pl.col("macd_dea")) &
         (pl.col("macd_dif").shift(1).over("symbol") <= pl.col("macd_dea").shift(1).over("symbol")))
            .alias("signal_macd_golden"),
        "signal_macd_dead": ((pl.col("macd_dif") < pl.col("macd_dea")) &
         (pl.col("macd_dif").shift(1).over("symbol") >= pl.col("macd_dea").shift(1).over("symbol")))
            .alias("signal_macd_dead"),
        "signal_ma20_breakout": ((pl.col("close") > pl.col("ma20")) &
         (pl.col("close").shift(1).over("symbol") <= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma20_breakout"),
        "signal_ma20_breakdown": ((pl.col("close") < pl.col("ma20")) &
         (pl.col("close").shift(1).over("symbol") >= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma20_breakdown"),
        "signal_ma5_breakout": ((pl.col("close") > pl.col("ma5")) &
         (pl.col("close").shift(1).over("symbol") <= pl.col("ma5").shift(1).over("symbol")))
            .alias("signal_ma5_breakout"),
        "signal_ma5_breakdown": ((pl.col("close") < pl.col("ma5")) &
         (pl.col("close").shift(1).over("symbol") >= pl.col("ma5").shift(1).over("symbol")))
            .alias("signal_ma5_breakdown"),
        "signal_ma10_breakout": ((pl.col("close") > pl.col("ma10")) &
         (pl.col("close").shift(1).over("symbol") <= pl.col("ma10").shift(1).over("symbol")))
            .alias("signal_ma10_breakout"),
        "signal_ma10_breakdown": ((pl.col("close") < pl.col("ma10")) &
         (pl.col("close").shift(1).over("symbol") >= pl.col("ma10").shift(1).over("symbol")))
            .alias("signal_ma10_breakdown"),
        "signal_n_day_high": (pl.col("close") >= pl.col("high_60d")).alias("signal_n_day_high"),
        "signal_n_day_low": (pl.col("close") <= pl.col("low_60d")).alias("signal_n_day_low"),
        "signal_boll_breakout_upper": (pl.col("close") > pl.col("boll_upper")).alias("signal_boll_breakout_upper"),
        "signal_boll_breakdown_lower": (pl.col("close") < pl.col("boll_lower")).alias("signal_boll_breakdown_lower"),
        "signal_volume_surge": (pl.col("vol_ratio_5d") >= 2.0).alias("signal_volume_surge"),
    }
    if want:
        df = df.with_columns([expressions[name] for name in SIGNAL_DEPENDENCIES if name in want])

    return df


def _apply_taiwan_limit_availability(df: pl.DataFrame) -> pl.DataFrame:
    """Keep unavailable classification distinct from a confirmed no-limit product."""
    is_taiwan = (pl.col("symbol").str.ends_with(".TWSE") | pl.col("symbol").str.ends_with(".TPEX")).fill_null(False)
    unknown = ~is_taiwan | pl.col("_tw_no_limit").is_null()
    no_limit = is_taiwan & pl.col("_tw_no_limit").fill_null(False)
    expressions = []
    for column in LIMIT_SIGNAL_OUTPUTS:
        if column not in df.columns or column == "turnover_rate":
            continue
        neutral = 0 if column.startswith("consecutive_") else False
        expressions.append(
            pl.when(unknown).then(None).when(no_limit).then(pl.lit(neutral))
            .otherwise(pl.col(column)).cast(df.schema[column]).alias(column)
        )
    return df.with_columns(expressions).drop("_tw_limit_pct", "_tw_no_limit", "_tw_tick_class")


def compute_limit_signals(
    df: pl.DataFrame,
    instruments: pl.DataFrame,
    needed: set[str] | None = None,
) -> pl.DataFrame:
    """計算漲跌停相關信號。

    產出:
      signal_limit_up, consecutive_limit_ups
      signal_limit_down, consecutive_limit_downs
      signal_limit_down_recovery (跌停翹板)
      signal_broken_limit_up (炸板: 最高價觸及漲停價但收盤未封住)

    輸入必須包含: symbol, date, raw_close, raw_high, raw_low, open, high, low, close,
                  change_pct, vol_ratio_5d。
    """
    if df.is_empty():
        return df

    want = set(LIMIT_SIGNAL_OUTPUTS) if needed is None else set(needed) & set(LIMIT_SIGNAL_OUTPUTS)
    if not want:
        return df

    need_up = bool(want & {"signal_limit_up", "consecutive_limit_ups", "signal_broken_limit_up"})
    need_down = bool(want & {"signal_limit_down", "consecutive_limit_downs", "signal_limit_down_recovery"})
    need_price_limits = need_up or need_down

    # 從 instruments 取風險警示標記、流通在外股數本(週轉率用)以及最新日漲跌停價
    inst_cols = ["symbol"]
    instrument_needs = set()
    if "turnover_rate" in want:
        instrument_needs.add("float_shares")
    if need_up:
        instrument_needs.add("limit_up")
    if need_down:
        instrument_needs.add("limit_down")
    for c in ["float_shares", "limit_up", "limit_down"]:
        if c not in instrument_needs:
            continue
        if c in instruments.columns:
            inst_cols.append(c)
    if need_price_limits and "as_of" in instruments.columns:
        inst_cols.append(
            pl.col("as_of").cast(pl.Date, strict=False).alias("_instrument_as_of")
        )
    inst_subset = instruments.select(inst_cols).unique(subset=["symbol"])
    inst_subset = inst_subset.join(taiwan_limit_profiles(instruments), on="symbol", how="left")

    df = df.join(inst_subset, on="symbol", how="left", suffix="_inst")

    if "turnover_rate" in want:
        # 延後匯入: 避免與 app.taiwan 循環匯入
        from app.taiwan.realtime.calendar import taipei_today

        df = apply_historical_float_shares(df, today=taipei_today())

    # Turnover (%): legacy volume is in lots; Taiwan volume and float_shares are shares.
    if "turnover_rate" in want and "float_shares" in df.columns and "volume" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("float_shares") > 0)
              .then(turnover_rate_pct_expr())
              .otherwise(None)
              .alias("turnover_rate")
        )
    elif "turnover_rate" in want and "turnover_rate" not in df.columns:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("turnover_rate"))

    # 前一日參考收盤價（交易所漲跌停基準價）
    # 僅在 adj_factor 發生變化（除權除息 XD/DR）時使用前復權昨收作為交易所參考價;
    # 否則使用原始 raw_close.shift(1) 以避免浮點精度誤差。
    if not need_price_limits:
        cleanup = [c for c in ("name", "float_shares", "limit_up", "limit_down") if c in df.columns]
        return _apply_taiwan_limit_availability(df).drop(cleanup)

    _adj_today = pl.col("close") / pl.col("raw_close")
    _adj_yesterday = pl.col("close").shift(1).over("symbol") / pl.col("raw_close").shift(1).over("symbol")
    _adj_changed = (_adj_today - _adj_yesterday).abs() > 1e-6
    df = df.with_columns(
        pl.when(_adj_changed)
        .then(pl.col("close").shift(1).over("symbol"))   # 除權: 使用前復權昨收
        .otherwise(pl.col("raw_close").shift(1).over("symbol"))  # 正常: 使用原始昨收
        .alias("_prev_raw_close")
    )

    df = df.with_columns(
        polars_price_limit_pct(pl.col("symbol"), taiwan_limit_pct=pl.col("_tw_limit_pct"))
        .alias("_limit_pct")
    )

    # 理論漲停價 = prev_close × (1 + limit_pct)  整數算術，避免浮點誤差
    df = df.with_columns(
        polars_limit_price(pl.col("_prev_raw_close"), pl.col("_limit_pct"), up=True,
                           tick_class=pl.col("_tw_tick_class"))
        .alias("_theoretical_limit_up")
    )

    # 理論跌停價 = prev_close × (1 - limit_pct)
    df = df.with_columns(
        polars_limit_price(pl.col("_prev_raw_close"), pl.col("_limit_pct"), up=False,
                           tick_class=pl.col("_tw_tick_class"))
        .alias("_theoretical_limit_down")
    )

    # 生效漲跌停價: 維表日期與行情日期一致時使用權威值, 否則使用理論價。
    # 舊版維表沒有 as_of, 保持僅在最新行情日使用權威值的相容行為。
    _SENTINEL = 10000.0
    if "_instrument_as_of" in df.columns:
        authoritative_date = (
            pl.col("_instrument_as_of") == pl.col("date").cast(pl.Date, strict=False)
        )
    else:
        authoritative_date = pl.col("date") == pl.col("date").max()
    if "limit_up" in df.columns:
        effective_limit_up = pl.when(
            authoritative_date
            & pl.col("limit_up").is_not_null()
            & (pl.col("limit_up") < _SENTINEL)
        ).then(pl.col("limit_up")).otherwise(pl.col("_theoretical_limit_up"))
    else:
        effective_limit_up = pl.col("_theoretical_limit_up")
    if "limit_down" in df.columns:
        effective_limit_down = pl.when(
            authoritative_date
            & pl.col("limit_down").is_not_null()
            & (pl.col("limit_down") < _SENTINEL)
        ).then(pl.col("limit_down")).otherwise(pl.col("_theoretical_limit_down"))
    else:
        effective_limit_down = pl.col("_theoretical_limit_down")
    effective_exprs: list[pl.Expr] = []
    if need_up:
        effective_exprs.append(effective_limit_up.alias("_effective_limit_up"))
    if need_down:
        effective_exprs.append(effective_limit_down.alias("_effective_limit_down"))
    df = df.with_columns(effective_exprs)

    # ── signal_limit_up ──
    if need_up:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_close") > 0)
        ).then(
            pl.col("raw_close") >= (pl.col("_effective_limit_up") - 0.005)
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_limit_up")
        )

    # ── consecutive_limit_ups ──
    if "consecutive_limit_ups" in want:
        df = df.with_columns(
        (~pl.col("signal_limit_up").fill_null(False))
        .cast(pl.UInt32)
        .cum_sum()
        .over("symbol")
        .alias("_grp_up")
        ).with_columns(
        pl.col("signal_limit_up")
        .cast(pl.UInt32)
        .cum_sum()
        .over("symbol", "_grp_up")
        .cast(pl.UInt32)
        .alias("consecutive_limit_ups")
        ).with_columns(
        pl.when(pl.col("signal_limit_up").fill_null(False))
        .then(pl.col("consecutive_limit_ups"))
        .otherwise(0)
        .cast(pl.UInt32)
        .alias("consecutive_limit_ups")
        )

    # ── signal_limit_down ──
    if need_down:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_close") > 0)
        ).then(
            pl.col("raw_close") <= (pl.col("_effective_limit_down") + 0.005)
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_limit_down")
        )

    # ── consecutive_limit_downs ──
    if "consecutive_limit_downs" in want:
        df = df.with_columns(
        (~pl.col("signal_limit_down").fill_null(False))
        .cast(pl.UInt32)
        .cum_sum()
        .over("symbol")
        .alias("_grp_down")
        ).with_columns(
        pl.col("signal_limit_down")
        .cast(pl.UInt32)
        .cum_sum()
        .over("symbol", "_grp_down")
        .cast(pl.UInt32)
        .alias("consecutive_limit_downs")
        ).with_columns(
        pl.when(pl.col("signal_limit_down").fill_null(False))
        .then(pl.col("consecutive_limit_downs"))
        .otherwise(0)
        .cast(pl.UInt32)
        .alias("consecutive_limit_downs")
        )

    # ── signal_limit_down_recovery (跌停翹板) ──
    # 條件: 當日最低價曾觸及跌停價 + 最終沒有跌停 + 收陽
    if "signal_limit_down_recovery" in want:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_low") > 0)
        ).then(
            (~pl.col("signal_limit_down").fill_null(False))              # 最終沒跌停
            & (pl.col("raw_low") <= pl.col("_effective_limit_down") + 0.005)  # 曾觸及跌停(原始價口徑, 跌停價為原始價基準)
            & (pl.col("close") > pl.col("open"))                          # 收陽
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_limit_down_recovery")
        )

    # ── signal_broken_limit_up (炸板) ──
    # 條件: 最高價曾觸及漲停價 + 最終沒有封住漲停
    if "signal_broken_limit_up" in want:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_high") > 0)
        ).then(
            (~pl.col("signal_limit_up").fill_null(False))               # 最終沒封住漲停
            & (pl.col("raw_high") >= pl.col("_effective_limit_up") - 0.005)  # 曾觸及漲停價
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_broken_limit_up")
        )

    # 清理臨時欄 + JOIN 引入的 instruments 欄 (不存入 enriched)
    cleanup = ["_prev_raw_close", "_limit_pct",
               "_theoretical_limit_up", "_theoretical_limit_down",
               "_effective_limit_up", "_effective_limit_down",
               "_grp_up", "_grp_down", "_instrument_as_of"]
    # 清理 join 產生的重複欄
    for c in df.columns:
        if c.endswith("_inst"):
            cleanup.append(c)
    # name / float_shares / limit_up / limit_down 只用於計算, 不存入 enriched
    for c in ["name", "float_shares", "limit_up", "limit_down"]:
        if c in df.columns and c != "turnover_rate":
            cleanup.append(c)
    internal_outputs = {"signal_limit_up", "signal_limit_down"} - want
    cleanup.extend(c for c in internal_outputs if c in df.columns)
    df = df.drop([c for c in cleanup if c in df.columns])

    return _apply_taiwan_limit_availability(df)


def compute_all(
    df: pl.DataFrame,
    instruments: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """從 OHLCV 計算全套指標 + 信號。一站式呼叫。

    輸入: symbol, date, open, high, low, close, volume, amount, raw_close
    """
    df = compute_indicators(df)
    df = compute_signals(df)
    if instruments is not None and not instruments.is_empty():
        df = compute_limit_signals(df, instruments)

    # 清理 NaN / Inf
    float_cols = [c for c in df.columns if df[c].dtype.is_float()]
    if float_cols:
        df = df.with_columns([
            pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
              .then(None)
              .otherwise(pl.col(c))
              .alias(c)
            for c in float_cols
        ])

    return df


def filter_halt_days(df: pl.DataFrame) -> pl.DataFrame:
    """過濾停牌日。

    停牌日的 open/high 必然為 0 (無集合競價)。注意 close 可能被資料來源
    填充為前收盤價而非 0, 因此不能用 "OHLC 全零" 判斷, 否則會漏過這類
    停牌記錄 (如風險警示撤銷的停牌日), 汙染 MA/ATR 等指標。
    """
    if df.is_empty() or "open" not in df.columns or "high" not in df.columns:
        return df
    return df.filter(~((pl.col("open") == 0) & (pl.col("high") == 0)))


# ================================================================
# Pipeline: 盤後全量計算 + 寫入
# ================================================================

def compute_enriched(
    raw: pl.DataFrame,
    factors: pl.DataFrame | None = None,
    instruments: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """對原始日 K 應用前復權 + 全量計算指標 + 信號, 產出完整 enriched (含全部指標欄)。

    輸入應包含至少: symbol, date, open, high, low, close, volume (可選 amount)。
    如果提供了 factors, 先應用前復權再算指標。
    如果提供了 instruments, 計算漲跌停信號和週轉率。
    """
    if raw.is_empty():
        return raw

    # 過濾停牌日 (會汙染指標計算)
    raw = filter_halt_days(raw)

    if raw.is_empty():
        return raw

    # 保留不復權原始價格（漲停/炸板/跌停判斷需用不復權價格）
    raw = raw.with_columns(
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
    )

    # 應用前復權（只改 open/high/low/close，raw_close 不受影響）
    if factors is not None and not factors.is_empty():
        raw = _apply_adj_factor(raw, factors)

    # 排序
    df = raw.sort(["symbol", "date"])

    # 全量計算指標 + 信號
    df = compute_all(df, instruments=instruments)

    return df


# ================================================================
# 盤中增量計算: 只算今天 5500 行 (不復算歷史)
# ================================================================

def compute_enriched_today(
    live_agg: pl.DataFrame,
    prev_enriched: pl.DataFrame,
    today_ohlcv: pl.DataFrame,
    instruments: pl.DataFrame | None = None,
    elapsed_minutes: float | None = None,
) -> pl.DataFrame:
    """用昨天的遞推狀態 + 今天的 OHLCV 增量計算今天的 enriched 資料。

    只處理 ~5500 行, 耗時 ~10-50ms (替代全量 compute_enriched 的 1.5-2s)。

    參數:
        live_agg:       repo.get_live_agg() — 包含所有遞推狀態 + 視窗聚合
        prev_enriched:  repo.get_enriched_latest() — 昨天的完整 enriched (用於信號交叉判斷)
        today_ohlcv:    今天的 OHLCV (symbol, date, open, high, low, close, volume, amount)
        instruments:    維表 (漲跌停/週轉率需要)
        elapsed_minutes: 當日已交易分鐘數(用於標準量比的時間折算)。
            None 或 0 表示不折算(盤後或時間不可用, 此時 volume 已是全天量)。

    回傳:
        今天的 enriched DataFrame (~5500 行, 64 欄)
    """
    if today_ohlcv.is_empty() or live_agg.is_empty():
        return pl.DataFrame()

    alpha = _ema_alpha

    # ---- JOIN: 今天的 OHLCV + 各股票最後一個有效交易日的遞推狀態 ----
    # 當日行情是主表, 復牌或新上市股票不能因為沒有歷史狀態而被靜默刪除。
    live_state = live_agg.with_columns(pl.lit(True).alias("_has_history_state"))
    df = today_ohlcv.join(live_state, on="symbol", how="left")
    has_history_state = pl.col("_has_history_state").fill_null(False)

    # ---- 前復權: 儲存原始價 → 調整 OHLCV ----
    df = df.with_columns([
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
    ])
    if "_adj_factor" in df.columns:
        af = pl.col("_adj_factor").fill_null(1.0)
        df = df.with_columns([
            (pl.col("open") * af).alias("open"),
            (pl.col("high") * af).alias("high"),
            (pl.col("low") * af).alias("low"),
            (pl.col("close") * af).alias("close"),
        ])

    # ---- volume 統一 Float64 ----
    df = df.with_columns(pl.col("volume").cast(pl.Float64))

    # ---- ex_rights: 盤中除權極罕見, 直接 false ----
    df = df.with_columns(pl.lit(False).alias("ex_rights"))

    # ---- 基礎漲跌 ----
    # prev_close: 有則直接用 (來自 API quote_extra, raw), 需要乘 adj_factor 對齊復權價
    if "prev_close" not in df.columns:
        prev_close = pl.col("close_right") if "close_right" in df.columns else pl.col("close")
        df = df.with_columns(prev_close.alias("prev_close"))
    elif "_adj_factor" in df.columns:
        # 儲存 API 原始前收盤價 (用於漲跌停價計算)
        df = df.with_columns(pl.col("prev_close").alias("_prev_close_raw"))
        # API 回傳的 prev_close 是原始價, 乘復權因子對齊復權價 (用於 change_pct)
        df = df.with_columns((pl.col("prev_close") * pl.col("_adj_factor").fill_null(1.0)).alias("prev_close"))

    # change_pct / change_amount / amplitude: 有則直接用, 無則計算
    if "change_pct" not in df.columns:
        df = df.with_columns((pl.col("close") / pl.col("prev_close") - 1).alias("change_pct"))
    if "change_amount" not in df.columns:
        df = df.with_columns((pl.col("close") - pl.col("prev_close")).alias("change_amount"))
    if "amplitude" not in df.columns:
        df = df.with_columns(
            pl.when(pl.col("prev_close") > 0)
              .then((pl.col("high") - pl.col("low")) / pl.col("prev_close"))
              .otherwise(None)
              .alias("amplitude"),
        )

    # ---- EMA (遞推) ----
    df = df.with_columns([
        (alpha(5)  * pl.col("close") + (1 - alpha(5))  * pl.col("ema5")).alias("ema5"),
        (alpha(10) * pl.col("close") + (1 - alpha(10)) * pl.col("ema10")).alias("ema10"),
        (alpha(20) * pl.col("close") + (1 - alpha(20)) * pl.col("ema20")).alias("ema20"),
        (alpha(30) * pl.col("close") + (1 - alpha(30)) * pl.col("ema30")).alias("ema30"),
        (alpha(60) * pl.col("close") + (1 - alpha(60)) * pl.col("ema60")).alias("ema60"),
    ])

    # ---- MACD (遞推) ----
    ema12 = alpha(12) * pl.col("close") + (1 - alpha(12)) * pl.col("_ema12")
    ema26 = alpha(26) * pl.col("close") + (1 - alpha(26)) * pl.col("_ema26")
    dif = ema12 - ema26
    dea = alpha(9) * dif + (1 - alpha(9)) * pl.col("macd_dea")
    df = df.with_columns([
        dif.alias("macd_dif"),
        dea.alias("macd_dea"),
        ((dif - dea) * 2).alias("macd_hist"),
    ])

    # ---- MA (用部分和) ----
    df = df.with_columns([
        ((pl.col("_ma5_partial_sum") + pl.col("close")) / 5).alias("ma5"),
        ((pl.col("_ma10_partial_sum") + pl.col("close")) / 10).alias("ma10"),
        ((pl.col("_ma20_partial_sum") + pl.col("close")) / 20).alias("ma20"),
        ((pl.col("_ma30_partial_sum") + pl.col("close")) / 30).alias("ma30"),
        ((pl.col("_ma60_partial_sum") + pl.col("close")) / 60).alias("ma60"),
    ])

    # ---- Bollinger ----
    boll_sum = pl.col("_boll_partial_sum") + pl.col("close")
    boll_sq_sum = pl.col("_boll_partial_sq_sum") + pl.col("close") ** 2
    boll_ma = boll_sum / 20
    boll_var = boll_sq_sum / 20 - boll_ma ** 2
    boll_std = pl.when(boll_var > 0).then(boll_var.sqrt()).otherwise(0.0)
    df = df.with_columns([
        (boll_ma + 2 * boll_std).alias("boll_upper"),
        (boll_ma - 2 * boll_std).alias("boll_lower"),
    ])

    # ---- KDJ (遞推) ----
    kdj_ln = pl.min_horizontal(pl.col("_kdj_8d_low"), pl.col("low"))
    kdj_hn = pl.max_horizontal(pl.col("_kdj_8d_high"), pl.col("high"))
    rsv = (pl.col("close") - kdj_ln) / (kdj_hn - kdj_ln).fill_null(1e-12) * 100
    k_today = rsv / 3 + pl.col("kdj_k") * 2 / 3
    d_today = k_today / 3 + pl.col("kdj_d") * 2 / 3
    df = df.with_columns([
        k_today.alias("kdj_k"),
        d_today.alias("kdj_d"),
        (3 * k_today - 2 * d_today).alias("kdj_j"),
    ])

    # ---- ATR (遞推) ----
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - pl.col("prev_close")).abs(),
        (pl.col("low") - pl.col("prev_close")).abs(),
    )
    df = df.with_columns(
        (tr / 14 + pl.col("atr_14") * 13 / 14).alias("atr_14"),
    )

    # ---- RSI (遞推, n=6,14,24) ----
    delta = pl.col("close") - pl.col("prev_close")
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    for n in (6, 14, 24):
        a = 1.0 / n
        avg_gain = (1 - a) * pl.col(f"_rsi_avg_gain_{n}") + a * gain
        avg_loss = (1 - a) * pl.col(f"_rsi_avg_loss_{n}") + a * loss
        df = df.with_columns([
            avg_gain.alias(f"_rsi_avg_gain_{n}"),
            avg_loss.alias(f"_rsi_avg_loss_{n}"),
            (100 - 100 / (1 + avg_gain / pl.when(avg_loss == 0).then(1e-12).otherwise(avg_loss)))
            .alias(f"rsi_{n}"),
        ])

    # ---- 量比 ----
    # vol_ma5/vol_ma10 保留原語意(含當天的均量), 其他地方在用
    vol_ma5 = (pl.col("_vol_ma5_partial_sum") + pl.col("volume")) / 5
    vol_ma10 = (pl.col("_vol_ma10_partial_sum") + pl.col("volume")) / 10
    # Taiwan volume ratio: accumulated volume / (previous mean daily volume * session fraction).
    # _vol_ma5_prev_sum 是前5個交易日成交量之和(tail(5)), 不含當天
    # 盤中 volume 是部分量, 按 elapsed_minutes 折算到全天量級
    vol_ma5_prev = pl.col("_vol_ma5_prev_sum") / 5  # 前5日均量(不含當天)
    if elapsed_minutes and elapsed_minutes > 0:
        from app.market_time import trading_minutes_per_session
        time_factor = trading_minutes_per_session() / elapsed_minutes
    else:
        time_factor = 1.0  # 盤後/無效時間: 不折算(此時 volume 已是全天量)
    df = df.with_columns([
        vol_ma5.alias("vol_ma5"),
        vol_ma10.alias("vol_ma10"),
        ((pl.col("volume") * time_factor) / vol_ma5_prev).alias("vol_ratio_5d"),
    ])

    # ---- 极值 60 日 ----
    df = df.with_columns([
        pl.when(has_history_state)
          .then(pl.max_horizontal(pl.col("_high_59d"), pl.col("high")))
          .otherwise(None)
          .alias("high_60d"),
        pl.when(has_history_state)
          .then(pl.min_horizontal(pl.col("_low_59d"), pl.col("low")))
          .otherwise(None)
          .alias("low_60d"),
    ])

    # ---- 動量 (5d/10d/20d/30d/60d) ----
    df = df.with_columns([
        (pl.col("close") / pl.col("_close_5d_ago") - 1).alias("momentum_5d"),
        (pl.col("close") / pl.col("_close_10d_ago") - 1).alias("momentum_10d"),
        (pl.col("close") / pl.col("_close_20d_ago") - 1).alias("momentum_20d"),
        (pl.col("close") / pl.col("_close_30d_ago") - 1).alias("momentum_30d"),
        (pl.col("close") / pl.col("_close_60d_ago") - 1).alias("momentum_60d"),
    ])

    # ---- 年化波動率 20d (遞推) ----
    # 用 Welford 簡化: sum + sum_sq of 19 historical returns + today's return
    today_ret = pl.col("close") / pl.col("prev_close") - 1
    total_sum = pl.col("_vol_19d_pct_sum").fill_null(0.0) + today_ret
    total_sq_sum = pl.col("_vol_19d_pct_sq_sum").fill_null(0.0) + today_ret ** 2
    vol_mean = total_sum / 20
    vol_var = total_sq_sum / 20 - vol_mean ** 2
    df = df.with_columns(
        pl.when(has_history_state & (vol_var > 0))
          .then(vol_var.sqrt() * (252 ** 0.5))
          .otherwise(None)
          .alias("annual_vol_20d"),
    )

    # ---- 信號 (需要昨天的指標值判斷交叉) ----
    if not prev_enriched.is_empty():
        sig_prev = prev_enriched.select(
            "symbol",
            pl.col("ma5").alias("_prev_ma5"),
            pl.col("ma10").alias("_prev_ma10"),
            pl.col("ma20").alias("_prev_ma20"),
            pl.col("ma60").alias("_prev_ma60"),
            pl.col("macd_dif").alias("_prev_dif"),
            pl.col("macd_dea").alias("_prev_dea"),
            pl.col("boll_upper").alias("_prev_boll_upper"),
            pl.col("boll_lower").alias("_prev_boll_lower"),
            pl.col("close").alias("_prev_close_enriched"),
        )
        df = df.join(sig_prev, on="symbol", how="left")

        df = df.with_columns([
            # MA 金叉/死叉
            ((pl.col("ma5") > pl.col("ma20")) & (pl.col("_prev_ma5") <= pl.col("_prev_ma20")))
                .alias("signal_ma_golden_5_20"),
            ((pl.col("ma5") < pl.col("ma20")) & (pl.col("_prev_ma5") >= pl.col("_prev_ma20")))
                .alias("signal_ma_dead_5_20"),
            ((pl.col("ma20") > pl.col("ma60")) & (pl.col("_prev_ma20") <= pl.col("_prev_ma60")))
                .alias("signal_ma_golden_20_60"),
            # MACD 金叉/死叉
            ((pl.col("macd_dif") > pl.col("macd_dea")) & (pl.col("_prev_dif") <= pl.col("_prev_dea")))
                .alias("signal_macd_golden"),
            ((pl.col("macd_dif") < pl.col("macd_dea")) & (pl.col("_prev_dif") >= pl.col("_prev_dea")))
                .alias("signal_macd_dead"),
            # MA20 突破/跌破
            ((pl.col("close") > pl.col("ma20")) & (pl.col("_prev_close_enriched") <= pl.col("_prev_ma20")))
                .alias("signal_ma20_breakout"),
            ((pl.col("close") < pl.col("ma20")) & (pl.col("_prev_close_enriched") >= pl.col("_prev_ma20")))
                .alias("signal_ma20_breakdown"),
            # MA5 突破/跌破
            ((pl.col("close") > pl.col("ma5")) & (pl.col("_prev_close_enriched") <= pl.col("_prev_ma5")))
                .alias("signal_ma5_breakout"),
            ((pl.col("close") < pl.col("ma5")) & (pl.col("_prev_close_enriched") >= pl.col("_prev_ma5")))
                .alias("signal_ma5_breakdown"),
            # MA10 突破/跌破
            ((pl.col("close") > pl.col("ma10")) & (pl.col("_prev_close_enriched") <= pl.col("_prev_ma10")))
                .alias("signal_ma10_breakout"),
            ((pl.col("close") < pl.col("ma10")) & (pl.col("_prev_close_enriched") >= pl.col("_prev_ma10")))
                .alias("signal_ma10_breakdown"),
            # BOLL 突破
            (pl.col("close") >= pl.col("boll_upper")).alias("signal_boll_breakout_upper"),
            (pl.col("close") <= pl.col("boll_lower")).alias("signal_boll_breakdown_lower"),
        ])

        df = df.drop([
            c for c in df.columns
            # _prev_close_raw 留給下方 _compute_limit_signals_today 算漲跌停價 (最後才清)
            if c.startswith("_prev_") and c not in {"_prev_consec_up", "_prev_consec_down", "_prev_close_raw"}
        ])

    # N日新高/新低 + 放量
    df = df.with_columns([
        (pl.col("close") >= pl.col("high_60d")).alias("signal_n_day_high"),
        (pl.col("close") <= pl.col("low_60d")).alias("signal_n_day_low"),
        (pl.col("vol_ratio_5d") >= 2.0).alias("signal_volume_surge"),
    ])

    # ---- 漲跌停 + 週轉率 + 炸板 + 連板 ----
    if instruments is not None and not instruments.is_empty():
        df = _compute_limit_signals_today(df, instruments)

    # ---- 清理內部欄 ----
    drop_cols = [
        "close_right", "high_right", "low_right", "_prev_close_raw",
        "_ma5_partial_sum", "_ma10_partial_sum", "_ma20_partial_sum",
        "_ma30_partial_sum", "_ma60_partial_sum",
        "_boll_partial_sum", "_boll_partial_sq_sum",
        "_high_59d", "_low_59d",
        "_close_5d_ago", "_close_10d_ago", "_close_20d_ago",
        "_close_30d_ago", "_close_60d_ago",
        "_vol_ma5_partial_sum", "_vol_ma10_partial_sum", "_vol_ma5_prev_sum",
        "_kdj_8d_low", "_kdj_8d_high",
        "_window_len",
        "_rsi_avg_gain_6", "_rsi_avg_loss_6",
        "_rsi_avg_gain_14", "_rsi_avg_loss_14",
        "_rsi_avg_gain_24", "_rsi_avg_loss_24",
        "_ema12", "_ema26",
        "_adj_factor",
        "_vol_19d_pct_sum", "_vol_19d_pct_sq_sum",
        "_prev_consec_up", "_prev_consec_down",
        "_has_history_state",
    ]
    df = df.drop([c for c in drop_cols if c in df.columns])

    # 清理 NaN / Inf
    float_cols = [c for c in df.columns if df[c].dtype.is_float()]
    if float_cols:
        df = df.with_columns([
            pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
              .then(None)
              .otherwise(pl.col(c))
              .alias(c)
            for c in float_cols
        ])

    return df


def _compute_limit_signals_today(df: pl.DataFrame, instruments: pl.DataFrame) -> pl.DataFrame:
    """盤中增量版的漲跌停/週轉率/炸板/連板計算。"""
    inst_cols = ["symbol"]
    for c in ["float_shares", "limit_up", "limit_down"]:
        if c in instruments.columns:
            inst_cols.append(c)
    if "as_of" in instruments.columns:
        inst_cols.append(
            pl.col("as_of").cast(pl.Date, strict=False).alias("_instrument_as_of")
        )
    inst_subset = instruments.select(inst_cols).unique(subset=["symbol"])
    inst_subset = inst_subset.join(taiwan_limit_profiles(instruments), on="symbol", how="left")
    df = df.join(inst_subset, on="symbol", how="left", suffix="_inst")

    # 週轉率: API 有則直接用, 無則從 float_shares 計算
    if "turnover_rate" not in df.columns:
        if "float_shares" in df.columns and "volume" in df.columns:
            df = df.with_columns(
                pl.when(pl.col("float_shares") > 0)
                  .then(turnover_rate_pct_expr())
                  .otherwise(None)
                  .alias("turnover_rate")
            )

    # 漲跌停 (用 raw_close / raw_high 和前一日原始收盤價)
    # 優先用 API 原始前收盤價, 回退到 close_right, 最後回退到 raw_close
    if "_prev_close_raw" in df.columns:
        if "close_right" in df.columns:
            prev_raw = pl.when(pl.col("_prev_close_raw").is_not_null()).then(pl.col("_prev_close_raw")).otherwise(pl.col("close_right"))
        else:
            prev_raw = pl.col("_prev_close_raw")
    elif "close_right" in df.columns:
        prev_raw = pl.col("close_right")
    else:
        prev_raw = pl.col("raw_close")
    from app.taiwan.realtime.calendar import taipei_today  # 延後匯入: 避免與 app.taiwan 循環匯入

    trade_date = pl.col("date") if "date" in df.columns else pl.lit(taipei_today())
    limit_pct = polars_price_limit_pct(
        pl.col("symbol"),
        taiwan_limit_pct=pl.col("_tw_limit_pct"),
    ).alias("_limit_pct")

    limit_up_price = polars_limit_price(prev_raw, limit_pct, up=True, tick_class=pl.col("_tw_tick_class"))
    limit_down_price = polars_limit_price(prev_raw, limit_pct, up=False, tick_class=pl.col("_tw_tick_class"))

    # 生效漲跌停價: 維表日期與行情日期一致時優先使用交易所權威值;
    # 維表過期或價格缺失時回退自算理論價。舊版無 as_of 維表保持相容。
    # 哨兵閾值 10000 用於識別 "新股無漲跌停限制" 的佔位值 (實際漲停價不可能上萬)。
    _SENTINEL = 10000.0
    authoritative_date = (
        pl.col("_instrument_as_of") == trade_date.cast(pl.Date, strict=False)
        if "_instrument_as_of" in df.columns
        else pl.lit(True)
    )
    has_authoritative_up = pl.lit(False)
    has_authoritative_down = pl.lit(False)
    no_price_limit = pl.lit(False)
    if "limit_up" in df.columns:
        has_authoritative_up = (
            authoritative_date
            & pl.col("limit_up").is_not_null()
            & (pl.col("limit_up") > 0)
            & (pl.col("limit_up") < _SENTINEL)
        )
        no_price_limit = (
            authoritative_date
            & pl.col("limit_up").is_not_null()
            & (pl.col("limit_up") >= _SENTINEL)
        )
        effective_limit_up = pl.when(
            has_authoritative_up
        ).then(pl.col("limit_up")).otherwise(limit_up_price)
    else:
        effective_limit_up = limit_up_price
    if "limit_down" in df.columns:
        has_authoritative_down = (
            authoritative_date
            & pl.col("limit_down").is_not_null()
            & (pl.col("limit_down") > 0)
            & (pl.col("limit_down") < _SENTINEL)
        )
        effective_limit_down = pl.when(
            has_authoritative_down
        ).then(pl.col("limit_down")).otherwise(limit_down_price)
    else:
        effective_limit_down = limit_down_price

    valid_prev_raw = prev_raw.is_not_null() & (prev_raw > 0)
    is_limit_up = (
        pl.when(no_price_limit)
          .then(False)
          .when((valid_prev_raw | has_authoritative_up) & (pl.col("raw_close") > 0))
          .then(pl.col("raw_close") >= (effective_limit_up - 0.005))
          .otherwise(None).cast(pl.Boolean)
    )
    is_limit_down = (
        pl.when(no_price_limit)
          .then(False)
          .when((valid_prev_raw | has_authoritative_down) & (pl.col("raw_close") > 0))
          .then(pl.col("raw_close") <= (effective_limit_down + 0.005))
          .otherwise(None).cast(pl.Boolean)
    )

    df = df.with_columns([
        is_limit_up.alias("signal_limit_up"),
        is_limit_down.alias("signal_limit_down"),
        # 跌停翹板
        pl.when(no_price_limit)
          .then(False)
          .when((valid_prev_raw | has_authoritative_down) & (pl.col("raw_low") > 0))
          .then(
              (~is_limit_down.fill_null(True))
              & (pl.col("raw_low") <= effective_limit_down + 0.005)
              & (pl.col("close") > pl.col("open"))
          ).otherwise(None).cast(pl.Boolean)
          .alias("signal_limit_down_recovery"),
        # 炸板: 最高價曾觸及漲停價 + 最終未封住
        pl.when(no_price_limit)
          .then(False)
          .when((valid_prev_raw | has_authoritative_up) & (pl.col("raw_high") > 0))
          .then(
              (~is_limit_up.fill_null(True))
              & (pl.col("raw_high") >= effective_limit_up - 0.005)
          ).otherwise(None).cast(pl.Boolean)
          .alias("signal_broken_limit_up"),
    ])

    # 連板數: 同向 +1, 不同向歸零
    # _prev_consec_up / _prev_consec_down 來自 live_agg (昨日 enriched)
    if "_prev_consec_up" not in df.columns:
        df = df.with_columns(pl.lit(0).cast(pl.UInt32).alias("_prev_consec_up"))
    if "_prev_consec_down" not in df.columns:
        df = df.with_columns(pl.lit(0).cast(pl.UInt32).alias("_prev_consec_down"))
    prev_up = pl.col("_prev_consec_up").fill_null(0).cast(pl.UInt32)
    prev_down = pl.col("_prev_consec_down").fill_null(0).cast(pl.UInt32)
    df = df.with_columns([
        pl.when(is_limit_up.fill_null(False))
          .then((prev_up + 1).cast(pl.UInt32))
          .otherwise(pl.lit(0).cast(pl.UInt32))
          .alias("consecutive_limit_ups"),
        pl.when(is_limit_down.fill_null(False))
          .then((prev_down + 1).cast(pl.UInt32))
          .otherwise(pl.lit(0).cast(pl.UInt32))
          .alias("consecutive_limit_downs"),
    ])

    # 清理
    cleanup = ["_limit_pct", "limit_up", "limit_down", "_instrument_as_of"]
    for c in df.columns:
        if c.endswith("_inst"):
            cleanup.append(c)
    for c in ["name", "float_shares"]:
        if c in df.columns:
            cleanup.append(c)
    df = df.drop([c for c in cleanup if c in df.columns])

    return _apply_taiwan_limit_availability(df)
