"""讀本機台股日線跑 VCP 偵測，輸出 JSON 與 Markdown 報告（不連網）。

用法（在 backend/ 下）：
    .venv\\Scripts\\python.exe -m app.taiwan.vcp.run 2330 2454 6488 [--as-of 2026-11-11] [--out DIR]
    .venv\\Scripts\\python.exe -m app.taiwan.vcp.run --all            # 全市場普通股

RS 百分位一律以全市場普通股計算，所以只查幾檔也會讀全市場日線。資料載入與型態選股頁共用 service.py。
移植來源與授權見 NOTICE.md。
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.taiwan.data_root import taiwan_data_root
from app.taiwan.vcp.detect import MIN_BARS
from app.taiwan.vcp.service import TaiwanVcpScanService

# 無法從本機資料或專案規則確認的台股差異，照原版處理並明確標示
UNVERIFIED_NOTES = [
    "漲跌幅 10%：漲停鎖死日成交量受限，突破量（1.5 倍 50 日均量）可能低估；樞紐距離 3/5/10/20% 區間沿用美股原值。",
    "Pre-filter 門檻沿用原版數值：股價 > 10 元、50 日均量 >= 200,000 股（200 張）；是否適合台股未確認。",
    "52 週高低點用還原後 252 根日線的最高/最低價計算，原版取自 FMP quote 的 yearHigh/yearLow。",
    "Trend Template 第 7 項用全市場 RS 百分位 > 70（原版用相對 SPY 的分級估計名次）。",
]


def run(symbols: list[str] | None = None, as_of: date | None = None) -> dict[str, Any]:
    """symbols 為 None 時跑全市場普通股；接受 2330 或 2330.TWSE。"""
    service = TaiwanVcpScanService()
    universe = service.universe()
    by_code = dict(universe.select("code", "symbol").iter_rows())
    names = dict(universe.select("symbol", "name").iter_rows())
    pool = sorted(universe["symbol"].to_list())
    targets = pool if symbols is None else list(dict.fromkeys(s if "." in s else by_code.get(s, s) for s in symbols))
    target = service.resolve_target(as_of)
    results, rs_size, reason = service.evaluate(target, targets, pool)
    if reason:
        raise SystemExit(f"{target} 無法掃描：{reason}")
    rows = []
    for sym in targets:
        r = results[sym]
        if r["status"] == "excluded":
            r = {**r, "signal": False, "data_issues": [], "no_trade_skipped": 0}
        rows.append({"symbol": sym, "name": names.get(sym, ""), **r})
    rows.sort(key=lambda r: (r.get("composite_score") is None, -(r.get("composite_score") or 0), r["symbol"]))
    return {"as_of": target.isoformat(), "rs_universe_size": rs_size, "min_bars": MIN_BARS,
            "unverified": UNVERIFIED_NOTES, "results": rows}


def _fmt(v: Any) -> str:
    return "—" if v is None else f"{v:,.2f}" if isinstance(v, float) else str(v)


def to_markdown(report: dict[str, Any], top: int = 50) -> str:
    results = report["results"]
    scored = [r for r in results if r.get("composite_score") is not None]
    shown = results[:top]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines = [f"# VCP 偵測報告（{report['as_of']}）", "",
             "資料：本機 TWSE/TPEX 日線（PIT 還原權息，成交量單位為股，略過無成交日）；"
             f"RS：{report['rs_universe_size']} 檔普通股的全市場百分位。只做型態偵測，不含回測或績效。", "",
             f"共 {len(results)} 檔：" + "、".join(f"{k} {v}" for k, v in counts.items())
             + f"；有訊號 {sum(r['signal'] for r in results)} 檔；有綜合分 {len(scored)} 檔。"
             + (f"下表只列綜合分前 {top} 檔，完整結果在 JSON。" if len(results) > top else ""), "",
             "| 代號 | 名稱 | 狀態 | 訊號 | 綜合分 | 評級 | 執行狀態 | 型態 | valid_vcp | RS 百分位 | 樞紐 | 停損 | 風險% | 距樞紐% |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in shown:
        lines.append("| " + " | ".join(_fmt(x) for x in (
            r["symbol"], r["name"], r["status"], "是" if r["signal"] else "否", r.get("composite_score"),
            r.get("rating"), r.get("execution_state"), r.get("pattern_type"), r.get("valid_vcp"),
            r.get("relative_strength", {}).get("rs_percentile"),
            r.get("pivot"), r.get("stop"), r.get("risk_pct"), r.get("distance_from_pivot_pct"))) + " |")
    lines += ["", "## 各檔細節", ""]
    for r in shown:
        lines.append(f"### {r['symbol']} {r['name']}")
        if "gates" not in r:
            lines += [f"- 不產生訊號：{r.get('reason')}"] + (["- 資料疑慮：" + "；".join(r["data_issues"])]
                                                            if r.get("data_issues") else []) + [""]
            continue
        g, cs = r["gates"], r["component_scores"]
        lines += [
            f"- 門檻：Pre-filter {'通過' if g['prefilter_passed'] else '未過'}、"
            f"Trend Template {r['trend_template']['criteria_passed']}/7（{'通過' if g['trend_template_passed'] else '未過'}）、"
            f"停滯股 {'是' if g['stale_price'] else '否'}",
            "- 分項：" + "、".join(f"{k} {v}" for k, v in cs.items()),
            "- 收縮：" + ("、".join(f"{c['label']} {c['depth_pct']}%（{c['duration_days']} 日）"
                                 for c in r["vcp_pattern"]["contractions"]) or "無"),
            f"- 量縮比 {r['volume_pattern'].get('dry_up_ratio')}；執行狀態理由：{r['execution_state_reason']}",
        ]
        if r["vcp_pattern"].get("validation") and r["vcp_pattern"]["validation"]["issues"]:
            lines.append("- 型態問題：" + "；".join(r["vcp_pattern"]["validation"]["issues"]))
        if r["cap_reason"]:
            lines.append(f"- 評級上限：{r['cap_reason']}")
        if r["no_trade_skipped"]:
            lines.append(f"- 視窗內略過 {r['no_trade_skipped']} 個無成交日")
        if r["data_issues"]:
            lines.append("- 資料疑慮（不產生訊號）：" + "；".join(r["data_issues"]))
        lines.append("")
    lines += ["## 台股差異與驗證狀態", "",
              "- PASS 成交量單位：本機 volume 為股（amount ÷ close ≈ volume），量能指標只用比值。",
              "- PASS 還原權息：用專案 PIT 還原（adjust_prices_as_of + CorporateActionStore）；未通過者排除（excluded）；量能視窗內有股數變動者也排除。",
              "- PASS 升降單位：停損向下、樞紐向上對齊 TickSizeModel 普通股級距；只掃上市櫃四碼普通股。",
              f"- PASS 相對強弱：Minervini 加權報酬在 {report['rs_universe_size']} 檔普通股間排百分位，排序等同以加權指數為基準（每檔減同一基準報酬不改變名次）；無法計算時綜合分標不可用，不當 0 分。",
              "- 說明 無成交日：交易所沒有 OHLC 的交易日不算 K 棒，取最近 260 個有成交的交易日。"]
    lines += [f"- UNVERIFIED {note}" for note in report["unverified"]]
    lines += ["", "來源：tradermonty/claude-trading-skills skills/vcp-screener（MIT，Copyright (c) 2026 TraderMonty）。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="本機台股 VCP 偵測")
    parser.add_argument("symbols", nargs="*", help="例如 2330 6488 或 2330.TWSE")
    parser.add_argument("--all", action="store_true", help="跑全市場普通股")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--top", type=int, default=50, help="Markdown 列出前幾檔（JSON 一律完整）")
    parser.add_argument("--out", type=Path, default=None, help="輸出目錄（預設 data/taiwan/vcp_reports）")
    args = parser.parse_args()
    if not args.all and not args.symbols:
        parser.error("請給股票代號，或用 --all 跑全市場")
    report = run(None if args.all else args.symbols, args.as_of)
    out = args.out or taiwan_data_root() / "vcp_reports"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"vcp_{report['as_of']}{'_all' if args.all else ''}"
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(to_markdown(report, args.top), encoding="utf-8")
    print(stem.with_suffix(".json"))
    print(stem.with_suffix(".md"))


if __name__ == "__main__":
    main()
