"""Offline weekly review of hashed decisions and contemporaneously locked legacy selections."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.taiwan.corporate_actions import event_market_open
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, taipei_now
from app.taiwan.selection_review_models import SelectionSnapshot
from app.taiwan.weekly_review_outcomes import WeeklyOutcomeEvaluator
from app.taiwan.weekly_review_stats import build_weekly_review
from app.taiwan.weekly_selection_review import (
    _check_window,
    _prior_picks,
    bound_batch_next_session,
    file_sha256,
    next_session,
    read_execution_opens,
    read_records,
)


def _saved_at(raw: str | None) -> datetime:
    if not raw:
        raise ValueError("missing_record_timestamp")
    stamp = datetime.fromisoformat(raw)
    if stamp.utcoffset() is None:
        raise ValueError("record_timestamp_needs_timezone")
    return stamp.astimezone(TAIPEI_TZ)


def _matches_formal_batch(row: dict, day: date, following: date, written: datetime,
                          batch: SelectionSnapshot) -> bool:
    strategy = {"id": batch.strategy_id, "name": batch.strategy_name, "version": batch.strategy_version,
                "rule_version": batch.rule_version or batch.strategy_id}
    return (row.get("formal_batch_id") == batch.snapshot_id and row.get("week") == day.strftime("%G-W%V")
            and day.isoformat() == batch.as_of_date == batch.source_data_date
            and following.isoformat() == batch.target_trade_date and batch.locked_at is not None
            and market_close(day) <= _saved_at(batch.locked_at) <= written
            and all(row["source_strategy"].get(k) == v for k, v in strategy.items()))


def _weekly_records(user_data: Path, cutoff: datetime, calendar: TaiwanTradingCalendar,
                    census: ObservedUniverseStore | None, batches: list[SelectionSnapshot]) -> tuple[list[dict], list[dict], list[dict]]:
    root = user_data / "weekly_selection"
    result, rejected, rejected_opens = [], [], []
    bindings = {}
    future_files = set()
    for batch in batches:
        if batch.record_type != "forward_batch":
            continue
        source = date.fromisoformat(batch.as_of_date)
        key = f"{source:%G-W%V}/{batch.strategy_id}.jsonl"
        if source > cutoff.date() or (batch.locked_at and _saved_at(batch.locked_at) > cutoff):
            future_files.add(key)
            continue
        if batch.weekly_records_sha256 is None and not batch.weekly_capture_error:
            continue
        if key in bindings:
            raise ValueError(f"duplicate_bound_weekly_batch: {key}")
        bindings[key] = batch
    files = {path.relative_to(root).as_posix(): path for path in root.glob("*/*.jsonl")
             if not path.name.endswith(".opens.jsonl")}
    for key in sorted((files.keys() - future_files) | bindings.keys()):
        batch = bindings.get(key)
        path = files.get(key)
        if batch is None:
            # A self-hashed file cannot identify its producer or prove it is complete.
            # Its content, including the claimed batch ID and dates, is untrusted.
            try:
                week_start = date.fromisoformat(f"{path.parent.name}-1")
            except ValueError:
                week_start = None
            if week_start is None or week_start <= cutoff.date():
                rejected.append({"file": key, "last_trading_day": None, "reason": "weekly_file_unbound"})
            continue
        details = {"file": key, "last_trading_day": batch.as_of_date, "formal_batch_id": batch.snapshot_id}
        if batch.weekly_capture_error:
            rejected.append({**details, "reason": batch.weekly_capture_error})
            continue
        if path is None or file_sha256(path) != batch.weekly_records_sha256:
            reason = "bound_weekly_file_missing" if path is None else "weekly_batch_digest_mismatch"
            rejected.append({**details, "reason": reason})
            continue
        try:
            records = read_records(path)
            stamps = [_saved_at(row["written_at"]) for row in records]
            following = bound_batch_next_session(records, batch, calendar, census)
        except (KeyError, ValueError) as exc:
            rejected.append({**details, "reason": str(exc)})
            continue
        if max(stamps) > cutoff:
            rejected.append({**details, "reason": "bound_weekly_file_not_available_as_of"})
            continue  # The complete atomic batch was not yet available.
        selected = set(batch.selected_symbols)
        for row, written in zip(records, stamps, strict=True):
            try:
                if row.get("signal_basis") != "daily_strategy_weekly_snapshot":
                    raise ValueError("unexpected_signal_basis")
                day = date.fromisoformat(row["last_trading_day"])
                _check_window(day, following, written)
                if not _matches_formal_batch(row, day, following, written, batch):
                    raise ValueError("weekly_formal_batch_mismatch")
                if row["decision"] == "signal" and row["symbol"] not in selected:
                    raise ValueError("weekly_signal_not_in_formal_selection")
            except (KeyError, ValueError) as exc:
                rejected.append({**details, "reason": str(exc),
                                 "last_trading_day": row.get("last_trading_day", batch.as_of_date),
                                 "written_at": written.isoformat()})
                break
        else:
            status = "pending" if cutoff < market_close(following) else "not_recorded"
            try:
                opens = read_execution_opens(path, records, following, cutoff)
            except (ValueError, OSError) as exc:
                rejected_opens.append({**details, "file": path.with_suffix(".opens.jsonl").relative_to(root).as_posix(),
                                       "reason": str(exc)})
                opens, status = {}, "invalid"
            for row in records:
                if row["decision"] == "signal":
                    observed = opens.get(row["record_sha256"], {})
                    row = {**row, "execution_session": following.isoformat(), "execution_open_status": observed.get("status", status),
                           "executable_reference_open": observed.get("executable_reference_open")}
                result.append(row)
    return result, rejected, rejected_opens


def _legacy_batches(user_data: Path) -> list[SelectionSnapshot]:
    path = user_data / "taiwan_selection_snapshots.json"
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(records, list):
        raise ValueError("selection_snapshot_list_required")
    return [SelectionSnapshot.model_validate(row) for row in records]


def _legacy_records(batches, cutoff, evaluator, calendar, census, frozen_keys):
    """Use saved membership and only those drops provable from earlier official batches."""
    result, admitted = [], []
    skipped = Counter()
    seen: set[tuple[date, str]] = set()
    for batch in batches:
        if batch.record_type != "forward_batch":
            skipped["research"] += 1
            continue
        source = date.fromisoformat(batch.as_of_date)
        if source > cutoff.date():
            skipped["after_cutoff"] += 1
            continue
        if not (batch.locked_at and batch.source_data_date == batch.as_of_date and batch.target_trade_date):
            skipped["missing_provenance"] += 1
            continue
        locked = _saved_at(batch.locked_at)
        if locked > cutoff:
            skipped["after_cutoff"] += 1
            continue
        if (source, batch.strategy_id) in frozen_keys:
            skipped["covered_by_weekly_ledger"] += 1
            continue
        if batch.weekly_capture_error or batch.weekly_records_sha256 is not None:
            # Only its bound file knows which selected stocks had missing core fields.
            skipped["bound_weekly_records_unavailable"] += 1
            continue
        if any(evaluator.week_close(source, market) != source for market in ("TWSE", "TPEX")):
            skipped["not_verified_week_close"] += 1
            continue
        following = next_session(source, calendar, census)
        if (following.isoformat() != batch.target_trade_date or
                not market_close(source) <= locked < event_market_open(following)):
            skipped["outside_lock_window"] += 1
            continue
        key = (source, batch.strategy_id)
        if key in seen:
            raise ValueError(f"duplicate_formal_batch: {batch.snapshot_id}")
        seen.add(key)
        symbols = [item.symbol for item in batch.items]
        if len(symbols) != len(set(symbols)) or set(symbols) != set(batch.selected_symbols):
            raise ValueError(f"invalid_frozen_membership: {batch.snapshot_id}")
        risk_day = None
        if batch.risk_source_as_of:
            raw = batch.risk_source_as_of
            risk_day = date.fromisoformat(raw) if len(raw) == 10 else _saved_at(raw).date()
        prior, prior_dates = _prior_picks(batches, batch.strategy_id, source - timedelta(days=source.weekday()),
                                          source, locked, calendar, census)
        dropped = prior.keys() - set(symbols)
        admitted.append({"snapshot_id": batch.snapshot_id, "date": source.isoformat(),
                         "strategy_id": batch.strategy_id, "strategy_name": batch.strategy_name,
                         "count": len(symbols), "locked_at": locked.isoformat(),
                         "prior_dates": sorted(d.isoformat() for d in prior_dates), "dropped_count": len(dropped),
                         "risk_date_after_close_day": risk_day > source if risk_day else None})
        common = {
            "week": source.strftime("%G-W%V"), "last_trading_day": source.isoformat(),
            "execution_session": following.isoformat(),
            "signal_basis": "daily_strategy_weekly_snapshot",
            "source_strategy": {"id": batch.strategy_id, "name": batch.strategy_name,
                                "version": batch.strategy_version, "rule_version": batch.rule_version},
            "pattern_tag": batch.strategy_id.rsplit("_v", 1)[0],
        }
        for item in batch.items:
            result.append({**common, "decision": "signal", "reason": "selected_at_week_close",
                           "last_selected_date": source.isoformat(), "symbol": item.symbol, "reference_close": item.price})
        for symbol in sorted(dropped):
            result.append({**common, "decision": "no_signal", "reason": "dropped_before_week_close",
                           "last_selected_date": prior[symbol].isoformat(), "symbol": symbol,
                           "reference_close": evaluator.official_close(symbol, source)})
    return result, admitted, dict(skipped)


_MISSING_REASON_TEXT = {
    "weekly_ledger_missing": "當週沒有寫入週快照",
    # The weekly row describes the close of the week's last session; evidence fetched
    # on a later day would be look-ahead for returns measured from that close.
    "future_snapshot_date": "週收盤日過後才補鎖，為避免用到事後資料而不寫入",
}


def _missing_weeks(start: date, end: date, calendar: TaiwanTradingCalendar, strategies: set[str],
                   covered: set[tuple[str, str]], rejected: list[dict]) -> list[dict]:
    """ISO weeks with (possible) sessions whose strategy has neither a usable ledger nor admitted legacy batch."""
    reasons: dict[tuple[str, str], list[str]] = {}
    for row in rejected:
        week, _, name = row["file"].partition("/")
        reasons.setdefault((week, name.removesuffix(".jsonl")), []).append(row["reason"])
    result = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        days = [monday + timedelta(days=i) for i in range(7)]
        # A ledger is written on the week's last session, so a week that can still
        # trade on or after `end` is not due yet rather than missing.
        due = not any(d >= end and calendar.is_trading_day(d) is not False for d in days)
        status = [calendar.is_trading_day(d) for d in days if d <= end]
        week = monday.strftime("%G-W%V")
        if due and any(s is not False for s in status):
            lacking = sorted(s for s in strategies if (week, s) not in covered)
            if lacking:
                result.append({"week": week, "strategies": lacking,
                               "sessions_verified": any(s is True for s in status),
                               "reasons": {s: reasons.get((week, s), ["weekly_ledger_missing"]) for s in lacking}})
        monday += timedelta(days=7)
    return result


def generate_report(data_dir: Path, as_of: date | None = None, *, max_gap_weeks: int = 2,
                    neutral_pct: float = 1.0, missed_pct: float = 8.0) -> dict[str, Any]:
    now = taipei_now()
    taiwan, user_data = data_dir / "taiwan", data_dir / "user_data"
    if not taiwan.is_dir() or not user_data.is_dir():
        raise ValueError("找不到台股資料或選股輸出目錄")
    calendar = TaiwanTradingCalendar()
    census = ObservedUniverseStore(taiwan / "observed_universe") if (taiwan / "observed_universe").is_dir() else None
    # Evidence captured overnight can already be known while today's price session has not closed.
    latest_requested = as_of is None
    as_of = as_of or now.date()
    if market_close(as_of) > now:
        if as_of > now.date():
            raise ValueError("報告基準日尚未收盤")
        closed_day = all((census.day_evidence(m, as_of, calendar=calendar) if census else calendar.day_evidence(as_of, m)).status
                         == "non_trading" for m in ("TWSE", "TPEX"))
        if not closed_day:
            if not latest_requested:
                raise ValueError("報告基準日尚未收盤")
            as_of -= timedelta(days=1)
    cutoff = now if latest_requested else min(now, datetime.combine(as_of, datetime.max.time(), TAIPEI_TZ))
    batches = _legacy_batches(user_data)
    ledger, rejected, rejected_opens = _weekly_records(user_data, cutoff, calendar, census, batches)
    starts = [date.fromisoformat(row["last_trading_day"]) for row in ledger]
    starts.extend(date.fromisoformat(b.as_of_date) for b in batches
                  if b.record_type == "forward_batch" and b.as_of_date <= as_of.isoformat())
    evaluator = WeeklyOutcomeEvaluator(taiwan, min(starts, default=as_of), as_of, calendar=calendar)
    frozen_keys = {(date.fromisoformat(r["last_trading_day"]), r["source_strategy"]["id"]) for r in ledger}
    historical, admitted, skipped = _legacy_records(batches, cutoff, evaluator, calendar, census, frozen_keys)
    params = {"max_gap_weeks": max_gap_weeks, "neutral_pct": neutral_pct, "missed_pct": missed_pct}
    weekly = build_weekly_review(ledger, evaluator.evaluate, **params)
    legacy_strategies = {b["strategy_id"]: b["strategy_name"] for b in admitted}
    history = build_weekly_review(historical, evaluator.evaluate, strategy_names=legacy_strategies, **params)
    # A continuous series may start in the legacy file and continue in the weekly ledger.
    combined = (build_weekly_review([*historical, *ledger], evaluator.evaluate, strategy_names=legacy_strategies, **params)
                if ledger and admitted else None)
    for stats in (history, combined):
        if stats:
            for row in stats["strategies"]:
                if row["strategy_id"] in legacy_strategies and not row["no_signal_completed_count"]:
                    row["missed_count"] = None
    covered = {(r["week"], r["source_strategy"]["id"]) for r in [*ledger, *historical]}
    strategies = {b.strategy_id for b in batches if b.record_type == "forward_batch" and b.as_of_date <= as_of.isoformat()}
    strategies |= {r["source_strategy"]["id"] for r in ledger}
    missing = _missing_weeks(min(starts), as_of, calendar, strategies, covered, rejected) if starts else []
    latest = max((p.parent.name.removeprefix("date=") for p in (taiwan / "daily").glob("date=*/part.parquet")
                  if p.parent.name.removeprefix("date=") <= as_of.isoformat()), default=None)
    return {"as_of": as_of.isoformat(), "evidence_cutoff": cutoff.isoformat(), "latest_daily_partition": latest, "parameters": params,
            "weekly_record_count": len(ledger), "historical_record_count": len(historical),
            "historical_batches": admitted, "skipped_batches": skipped, "rejected_weekly_batches": rejected,
            "rejected_execution_opens": rejected_opens, "missing_weeks": missing,
            "weekly": weekly, "historical": history,
            "historical_drops": [{k: r[k] for k in ("symbol", "source_strategy", "last_trading_day", "last_selected_date",
                                                    "reference_close", "reason")} for r in historical if r["decision"] == "no_signal"],
            "combined": combined}


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value).replace("|", "／").replace("\n", " ")


def _table_row(*values) -> str:
    return "| " + " | ".join(_cell(v) for v in values) + " |"


def render_markdown(report: dict) -> str:
    params = report["parameters"]
    lines = [
        f"# 台股每週選股復盤　{report['as_of']}", "",
        f"官方日線最新分區為 {report['latest_daily_partition'] or '無資料'}。表中報酬與勝率以百分比表示，超額為個股與同期指數的報酬率差。",
        f"同代號、原日線策略與型態距上次入選最多 {params['max_gap_weeks']} 個 ISO 週即合併，以首次入選週的收盤價起算。"
        "驗證週數按實際有交易的週計，主要統計採 4 週。",
        f"4 週報酬絕對值小於 {params['neutral_pct']:g}% 歸為 NEUTRAL。未發訊且 4 週漲幅至少 {params['missed_pct']:g}% 計為 MISSED。"
        "策略核心欄位資料不足的紀錄無從判定是否過濾太嚴，另列筆數，不計入 MISSED。"
        "勝率以 WIN 組數除以可評估組數，含 NEUTRAL。期望值採每組等權的實現報酬，因此等於樣本平均。"
        "未到期與無法評估的組別均排除於績效分母。",
        "MFE、MAE 分別採起點收盤後至期末的最高、最低日價相對報酬。遇除權息採已核實的還原因子，一致調整收盤和高低價。"
        "櫃買逐日公告可確認停牌。上市個股若缺行情且缺停牌證據，標示缺官方行情並排除該期間。", "",
    ]
    for rejected in report.get("rejected_weekly_batches", []):
        day = f"，週收盤日 {rejected['last_trading_day']}" if rejected.get("last_trading_day") else ""
        stamp = f"，寫入時間 {rejected['written_at']}" if rejected.get("written_at") else ""
        lines.extend([f"拒收週快照 `{rejected['file']}`{day}{stamp}"
                      f"（{rejected['reason']}）。", ""])
    if report.get("evidence_cutoff", "")[:10] > report["as_of"]:
        lines.extend([f"行情評估至 {report['as_of']}，選股紀錄讀取至 {report['evidence_cutoff']}。", ""])
    for rejected in report.get("rejected_execution_opens", []):
        lines.extend([f"開盤觀測檔 `{rejected['file']}` 未通過驗證（{rejected['reason']}），原週快照仍可評估。", ""])
    if report.get("missing_weeks"):
        lines.extend(["## 缺少週快照的週別", "",
                      "以下週別有交易日（或交易日尚未核實），但沒有可用的週快照，結果為缺資料，不代表零筆或未發訊。", "",
                      "| 週別 | 策略 | 交易日已核實 | 原因 |", "|---|---|---|---|"])
        for row in report["missing_weeks"]:
            by_reason: dict[str, list[str]] = {}
            for strategy in row["strategies"]:
                reason = "、".join(_MISSING_REASON_TEXT.get(code, code) for code in row["reasons"][strategy])
                by_reason.setdefault(reason, []).append(strategy)
            for reason, names in by_reason.items():
                lines.append(_table_row(row["week"], "、".join(names), "是" if row["sessions_verified"] else "否", reason))
        lines.append("")
    descriptive = ("combined", "歷史名單與 T3 週快照的連續復盤") if report.get("combined") else (
        "historical", "歷史正式日選股的週收盤回顧")
    for source, title in (("weekly", "T3 正式週快照"), descriptive):
        stats = report[source]
        count = report["weekly_record_count" if source == "weekly" else "historical_record_count"]
        lines.extend([f"## {title}", ""])
        if source != "weekly":
            if source == "combined":
                count += report["weekly_record_count"]
            batches = report["historical_batches"]
            late = sum(b["risk_date_after_close_day"] is True for b in batches)
            lines.extend([
                f"納入 {len(batches)} 個該週收盤後、下次開盤前鎖定的舊批次，風險來源日期晚於交易日的有 {late} 批。"
                "型態沿用原日線策略類別。舊名單只保存入選者，可比對同週已保存的日名單辨認落選。"
                "舊批次的 MISSED 僅涵蓋這些可核對的落選，完整數量未知。",
                "",
            ])
            by_day = Counter()
            for b in batches:
                by_day[b["date"]] += b["count"]
            if by_day:
                lines.extend(["來源日與入選筆數　" + "、".join(f"{d}（{n} 筆）" for d, n in sorted(by_day.items())), ""])
            if source == "combined":
                lines.extend(["同策略、同週以 T3 快照優先。以下統計涵蓋兩種來源，跨來源連續入選仍併入同一組。", ""])
        if not stats["strategies"]:
            lines.extend(["本期尚無符合條件的紀錄。", ""])
            continue
        primary = [outcome for g in stats["groups"] for horizon, outcome in g["outcomes"].items() if int(horizon) == 4]
        status = Counter(o["status"] for o in primary)
        unavailable_bench = sum(o["status"] == "completed" and o.get("benchmark_return_pct") is None for o in primary)
        no_signal = sum(s["no_signal_count"] for s in stats["strategies"])
        no_signal_due = sum(s["no_signal_completed_count"] for s in stats["strategies"])
        incomplete = sum(s["data_incomplete_count"] for s in stats["strategies"])
        lines.extend([f"共 {count} 筆觀測，其中 {sum(r['raw_signal_count'] for r in stats['strategies'])} 筆入選"
                      f"合併為 **{len(stats['groups'])} 組**。4 週可評估 {status['completed']} 組，"
                      f"待到期 {status['pending']} 組，到期但資料不足 {status['unavailable']} 組。"
                      f"另有 {no_signal} 筆已知未發訊，其中 {no_signal_due} 筆可評估 MISSED；"
                      f"{incomplete} 筆因資料不足未判定。", ""])
        if unavailable_bench:
            lines.extend([f"{unavailable_bench} 個可評估組缺少起點或期末的官方指數收盤，該組的指數與超額報酬留白。", ""])
        lines.extend(["| 策略 | 入選筆數 | 組數 | 可評估 | 勝率 % | 平均報酬 % | 期望值 % | 最大單組虧損 % | pending | 不可評估 | 已知 MISSED | 樣本 |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"])
        for s in stats["strategies"]:
            lines.append(_table_row(s["strategy_name"], s["raw_signal_count"], s["group_count"], s["completed_count"],
                                    s["win_rate_pct"], s["mean_return_pct"], s["expectancy_pct"], s["max_loss_pct"],
                                    s["pending_count"], s["unavailable_count"], s["missed_count"], s["sample_warning"] or ""))
        lines.extend(["", "### 各組前瞻結果", "",
                      "| 代號 | 原策略／型態 | 起點 | 起點收盤 | 次交易日 | 可執行參考開盤價 | 入選週別 | 驗證週數 | 期末 | 狀態 | 報酬 % | 指數 | 指數報酬 % | 超額 % | MFE % | MAE % | 價格基準 | 分類／註記 |",
                      "|---|---|---|---:|---|---:|---|---:|---|---|---:|---|---:|---:|---:|---:|---|---|"])
        for group in stats["groups"]:
            execution_open = group.get("executable_reference_open")
            if execution_open is None:
                execution_open = {"pending": "待公告", "official_open_unavailable": "無官方開盤價",
                                  "invalid": "觀測驗證失敗", "not_recorded": "無觀測紀錄"}.get(group.get("execution_open_status"), "—")
            for horizon, outcome in group["outcomes"].items():
                note = "、".join(filter(None, [group["classification"] if int(horizon) == 4 else None,
                                              *outcome.get("flags", [])]))
                lines.append(_table_row(group["symbol"], f"{group['strategy_id']}／{group['pattern_tag']}",
                                        group["start_day"], group["reference_close"], group.get("execution_session"),
                                        execution_open, "、".join(group["weeks"]), horizon, outcome.get("end_date"),
                                        outcome["status"], outcome.get("return_pct"), outcome.get("benchmark_symbol"),
                                        outcome.get("benchmark_return_pct"), outcome.get("excess_return_pct"),
                                        outcome.get("mfe_pct"), outcome.get("mae_pct"), outcome.get("price_basis"), note))
        lines.append("")
        if stats["missed"]:
            lines.extend(["### 未發訊後上漲的觀測", "", "| 代號 | 策略 | 日期 | 4 週報酬 % |", "|---|---|---|---:|"])
            for miss in stats["missed"]:
                lines.append(_table_row(miss["symbol"], miss["strategy_id"], miss["start_day"], miss["outcome"]["return_pct"]))
            lines.append("")
        if source != "weekly" and report.get("historical_drops"):
            lines.extend(["### 舊名單可核對的週內落選", "", "| 代號 | 原策略 | 最後入選日 | 週收盤日 | 理由 |", "|---|---|---|---|---|"])
            for row in report["historical_drops"]:
                lines.append(_table_row(row["symbol"], row["source_strategy"]["name"], row["last_selected_date"],
                                        row["last_trading_day"], row["reason"]))
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="以本機官方資料輸出每週選股復盤")
    parser.add_argument("--data-dir", type=Path, required=True, help="含 taiwan 與 user_data 的資料目錄")
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--max-gap-weeks", type=int, default=2)
    parser.add_argument("--neutral-pct", type=float, default=1.0)
    parser.add_argument("--missed-pct", type=float, default=8.0)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = generate_report(args.data_dir, args.as_of, max_gap_weeks=args.max_gap_weeks,
                             neutral_pct=args.neutral_pct, missed_pct=args.missed_pct)
    result = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) if args.format == "json" else render_markdown(report)
    if args.output is None:
        print(result)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf8")
        print(args.output.resolve())


if __name__ == "__main__":
    main()
