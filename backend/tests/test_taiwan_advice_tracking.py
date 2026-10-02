from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.advice_tracking import (
    AdviceTrackingService,
    ReflectionCreate,
)
from app.taiwan.ai_advice import (
    TaiwanAIAdvice,
    TaiwanAIAdviceResponse,
    TaiwanAIAdviceRun,
    TaiwanAIReview,
    TaiwanAIReviewIssue,
    TaiwanAIReviewRun,
)
from app.taiwan.ai_research import _evidence_digest
from app.taiwan.ai_research_history import (
    AIResearchHistoryError,
    TaiwanAIResearchHistoryStore,
)
from app.taiwan.buy_point import BuyPointSignal, builtin_presets
from app.taiwan.buy_point_service import strategy_definition_payload
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.quant.live_contract import canonical_hash
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.trade_plan import TradePlanEvaluation, TradePlanOutcome, build_trade_plan

EVIDENCE_DAY = date(2026, 10, 1)
FORWARD_DAY = date(2026, 10, 2)


def _plan(symbol: str = "2330.TWSE"):
    signal = BuyPointSignal(
        strategy_id="quant_pullback",
        symbol=symbol,
        detected_at="2026-10-01T13:30:00+08:00",
        data_as_of=EVIDENCE_DAY.isoformat(),
        status="triggered",
        price=100,
        entry_zone_low=95,
        entry_zone_high=98,
        quant_score=80,
        explanation="test",
        freshness="daily_cached",
    )
    return build_trade_plan(
        signal,
        instrument_type="stock",
        stop_reference_price=90,
        entry_window_days=2,
        max_holding_days=2,
    ), signal


def _save_advice(
    store: TaiwanAIResearchHistoryStore,
    *,
    symbol: str = "2330.TWSE",
    run_id: str | None = None,
):
    plan, signal = _plan(symbol)
    run_id = run_id or f"run-{symbol}"
    evidence = {"identity": {"symbol": symbol}, "price_context": {"trade_date": "2026-10-01"}}
    digest = _evidence_digest(evidence)
    advice = TaiwanAIAdvice(
        symbol=symbol,
        strategy_id=plan.strategy_id,
        action="buy",
        summary="test",
        buy_point_signal=signal,
        selected_trade_plan=plan,
        evidence_as_of=EVIDENCE_DAY.isoformat(),
    )
    response = TaiwanAIAdviceResponse(
        status="success",
        advice=advice,
        run_id=run_id,
        started_at="2026-10-01T14:00:00+08:00",
        completed_at="2026-10-01T14:01:00+08:00",
        generated_at="2026-10-01T14:01:00+08:00",
        evidence_as_of=EVIDENCE_DAY.isoformat(),
    )
    strategy_definition = strategy_definition_payload(builtin_presets()[0])
    run = TaiwanAIAdviceRun(
        run_id=run_id,
        response=response,
        evidence_payload=evidence,
        evidence_digest=digest,
        evidence_cutoff="2026-10-01T13:30:00+08:00",
        evidence_admission_provenance={
            "market_evidence_as_of": "2026-10-01",
            "buy_point_evidence_as_of": "2026-10-01",
            "candidate_plan_source": "server",
        },
        source_snapshot_id=f"taiwan_research:{symbol}:2026-10-01:{digest[:16]}",
        source_snapshot_digest=digest,
        strategy_definition_digest=canonical_hash(strategy_definition),
        strategy_definition=strategy_definition,
        trade_plan=plan,
        plan_identity=plan.plan_identity,
        plan_instance_id=plan.plan_instance_id,
        selected_plan_instance_id=plan.plan_instance_id,
        forward_cutoff="2026-10-02T09:00:00+08:00",
    )
    return store.ensure_advice(run), run, plan


def _save_review(
    store: TaiwanAIResearchHistoryStore,
    run: TaiwanAIAdviceRun,
    *,
    started_at: str = "2026-10-01T14:01:01+08:00",
    completed_at: str = "2026-10-01T14:02:00+08:00",
    blocking: bool = False,
):
    review = TaiwanAIReview(
        advice_run_id=run.run_id,
        issues=(
            [TaiwanAIReviewIssue(kind="blocking", text="blocked", evidence_refs=["x"])]
            if blocking
            else []
        ),
        no_material_issues=not blocking,
    )
    return store.ensure_review(
        run,
        TaiwanAIReviewRun(
            run_id=f"review-{run.run_id}-{completed_at}",
            advice_run_id=run.run_id,
            review=review,
            started_at=started_at,
            completed_at=completed_at,
        ),
    )


class FixedEvaluator:
    def __init__(
        self,
        status: str = "triggered",
        gross_return_pct: float | None = 5.0,
        *,
        bars_version: str = "bars-v1",
        calendar_version: str = "calendar-v1",
        actions_version: str = "actions-v1",
        terminal_day: date = FORWARD_DAY,
    ):
        self.status = status
        self.gross_return_pct = gross_return_pct
        self.bars_version = bars_version
        self.calendar_version = calendar_version
        self.actions_version = actions_version
        self.terminal_day = terminal_day

    def evaluate_with_provenance(self, plan, *, evaluated_as_of):
        terminal = self.terminal_day if self.status in {"triggered", "not_triggered", "undeterminable"} else None
        outcome = TradePlanOutcome(
            plan_identity=plan.plan_identity,
            plan_instance_id=plan.plan_instance_id,
            symbol=plan.symbol,
            evidence_as_of=plan.evidence_as_of,
            evaluated_as_of=evaluated_as_of,
            status=self.status,
            reason=None,
            price_adjustment_semantics=plan.price_adjustment_semantics,
            exit_session=self.terminal_day if self.status == "triggered" else None,
            gross_return_pct=self.gross_return_pct if self.status == "triggered" else None,
            terminal_session=terminal,
        )
        bars = {"version": self.bars_version}
        sessions = {"version": self.calendar_version}
        actions = {"version": self.actions_version}
        return TradePlanEvaluation(
            outcome=outcome,
            daily_bars_provenance=bars,
            daily_bars_digest=canonical_hash(bars),
            session_evidence_provenance=sessions,
            session_evidence_digest=canonical_hash(sessions),
            company_action_provenance=actions,
            company_action_digest=canonical_hash(actions),
        )


def _service(tmp_path, status="triggered", gross_return_pct=5.0):
    history = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")
    calendar = TaiwanTradingCalendar(known_trading_days={FORWARD_DAY})
    return AdviceTrackingService(
        history=history,
        calendar=calendar,
        census=ObservedUniverseStore(tmp_path / "census"),
        evaluator=FixedEvaluator(status, gross_return_pct),
    )


def test_forward_admission_keeps_raw_separate_from_late_and_blocking_review(tmp_path):
    service = _service(tmp_path)
    advice, run, _plan_value = _save_advice(service.history)
    assert service.forward_status(advice["id"]).status == "admitted"

    _save_review(service.history, run, blocking=True)
    reviewed = service.forward_status(advice["id"], view="reviewed")
    assert reviewed.status == "unavailable"
    assert "review_blocking" in reviewed.reasons
    assert service.forward_status(advice["id"], view="raw").status == "admitted"

    late_service = _service(tmp_path / "late")
    late, late_run, _ = _save_advice(late_service.history)
    _save_review(late_service.history, late_run, completed_at="2026-10-02T09:00:00+08:00")
    late_status = late_service.forward_status(late["id"], view="reviewed")
    assert "review_completed_after_forward_cutoff" in late_status.reasons
    assert late_service.forward_status(late["id"], view="raw").status == "admitted"


def test_raw_and_reviewed_outcomes_remain_separate_cohorts(tmp_path):
    service = _service(tmp_path)
    advice, run, plan = _save_advice(service.history)
    _save_review(service.history, run)

    raw = service.evaluate_outcome(
        advice["id"], evaluated_as_of=date(2026, 10, 5), view="raw"
    )
    reviewed = service.evaluate_outcome(
        advice["id"], evaluated_as_of=date(2026, 10, 5), view="reviewed"
    )

    assert raw["id"] != reviewed["id"]
    assert {raw["view"], reviewed["view"]} == {"raw", "reviewed"}
    assert service.statistics(plan.plan_identity, view="raw").outcome_status_counts["triggered"] == 1
    assert service.statistics(
        plan.plan_identity, view="reviewed"
    ).outcome_status_counts["triggered"] == 1


def test_outcome_input_digest_covers_bars_calendar_actions_and_provenance(tmp_path):
    service = _service(tmp_path)
    digests = []
    variants = (
        FixedEvaluator(bars_version="bars-v2"),
        FixedEvaluator(calendar_version="calendar-v2"),
        FixedEvaluator(actions_version="actions-v2"),
    )
    for index, evaluator in enumerate(variants):
        advice, _run, _plan_value = _save_advice(
            service.history,
            run_id=f"provenance-{index}",
        )
        service.evaluator = evaluator
        record = service.evaluate_outcome(
            advice["id"], evaluated_as_of=date(2026, 10, 5)
        )
        digests.append(record["input_digest"])
        for prefix in ("daily_bars", "session_evidence", "company_action"):
            assert canonical_hash(record[f"{prefix}_provenance"]) == record[f"{prefix}_digest"]
    assert len(set(digests)) == 3


def test_review_time_order_starts_after_advice_completion(tmp_path):
    service = _service(tmp_path)
    advice, run, _plan_value = _save_advice(service.history)
    _save_review(
        service.history,
        run,
        started_at="2026-10-01T13:59:00+08:00",
        completed_at="2026-10-01T14:02:00+08:00",
    )
    status = service.forward_status(advice["id"], view="reviewed")
    assert status.status == "unavailable"
    assert "review_time_order_invalid" in status.reasons


def test_reviewed_outcome_freezes_supporting_review_and_ignores_later_record(tmp_path):
    service = _service(tmp_path)
    advice, run, plan = _save_advice(service.history)
    review = _save_review(service.history, run)
    frozen = service.evaluate_outcome(
        advice["id"], evaluated_as_of=date(2026, 10, 5), view="reviewed"
    )
    assert frozen["supporting_review_id"] == review["id"]
    assert frozen["supporting_review_digest"] == review["review_digest"]

    with pytest.raises(AIResearchHistoryError, match="already has"):
        _save_review(
            service.history,
            run,
            completed_at="2026-10-01T14:03:00+08:00",
        )

    later = dict(review)
    later.update(
        id="review_later_tampered",
        run_id="review-later-tampered",
        completed_at="2026-10-02T09:30:00+08:00",
        review={
            "advice_run_id": run.run_id,
            "issues": [{"kind": "blocking", "text": "late", "evidence_refs": ["x"]}],
            "no_material_issues": False,
            "prompt_version": review["review"]["prompt_version"],
        },
        review_digest="tampered",
    )
    with service.history.path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(later, ensure_ascii=False, sort_keys=True) + "\n")

    assert service.forward_status(advice["id"], view="reviewed").status == "unavailable"
    stats = service.statistics(plan.plan_identity, view="reviewed")
    assert stats.outcome_status_counts["triggered"] == 1
    assert stats.integrity_conflicts == 0


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda row: row.update(evidence_cutoff="2026-10-01T13:30:00"), "missing_or_naive_timestamp"),
        (lambda row: row.pop("evidence_admission_provenance"), "missing_evidence_admission_provenance"),
        (lambda row: row["trade_plan"].update(stop_price=89), "plan_instance_id_mismatch"),
        (
            lambda row: row["strategy_definition"].update(enabled=False),
            "invalid_strategy_definition_digest",
        ),
    ],
)
def test_forward_admission_fails_closed_for_naive_missing_provenance_and_plan_tamper(
    tmp_path, mutation, reason
):
    service = _service(tmp_path)
    advice, _run, _ = _save_advice(service.history)
    rows = [json.loads(line) for line in service.history.path.read_text(encoding="utf-8").splitlines()]
    mutation(rows[0])
    service.history.path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    status = service.forward_status(advice["id"])
    assert status.status == "unavailable"
    assert reason in status.reasons


def test_forward_admission_rejects_response_trade_plan_content_tamper(tmp_path):
    service = _service(tmp_path)
    advice, _run, _ = _save_advice(service.history)
    rows = [json.loads(line) for line in service.history.path.read_text(encoding="utf-8").splitlines()]
    rows[0]["response"]["advice"]["selected_trade_plan"]["stop_price"] = 89
    service.history.path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    status = service.forward_status(advice["id"])
    assert status.status == "unavailable"
    assert "response_trade_plan_id_mismatch" in status.reasons
    assert "response_trade_plan_mismatch" in status.reasons


def test_forward_admission_rechecks_frozen_cutoff_against_current_census(tmp_path):
    history = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")
    advice, _run, _ = _save_advice(history)
    unresolved = AdviceTrackingService(
        history=history,
        calendar=TaiwanTradingCalendar(),
        census=ObservedUniverseStore(tmp_path / "census"),
        evaluator=FixedEvaluator(),
    ).forward_status(advice["id"])
    assert unresolved.status == "unavailable"
    assert unresolved.reasons == ["forward_session_unresolved"]

    closed = AdviceTrackingService(
        history=history,
        calendar=TaiwanTradingCalendar(known_holidays={FORWARD_DAY}),
        census=ObservedUniverseStore(tmp_path / "closed-census"),
        evaluator=FixedEvaluator(),
    ).forward_status(advice["id"])
    assert closed.status == "unavailable"
    assert closed.reasons == ["forward_cutoff_not_trading"]


@pytest.mark.parametrize(
    "status",
    ["triggered", "not_triggered", "immature", "data_insufficient", "undeterminable"],
)
def test_all_outcome_statuses_are_persisted_with_explicit_maturity(tmp_path, status):
    service = _service(tmp_path / status, status=status)
    advice, _run, _ = _save_advice(service.history)
    saved = service.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 5))
    assert saved["outcome"]["status"] == status
    assert (saved["outcome_matured_at"] is not None) == (
        status in {"triggered", "not_triggered", "undeterminable"}
    )
    if status == "data_insufficient":
        assert saved["outcome"]["gross_return_pct"] is None


def test_outcome_append_restart_truncated_tail_idempotency_and_terminal_conflict(tmp_path):
    service = _service(tmp_path)
    advice, _run, _ = _save_advice(service.history)
    first = service.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 5))
    with service.history.path.open("ab") as stream:
        stream.write(b'{"id":"truncated"')
    restarted = AdviceTrackingService(
        history=TaiwanAIResearchHistoryStore(service.history.path),
        calendar=TaiwanTradingCalendar(known_trading_days={FORWARD_DAY}),
        census=ObservedUniverseStore(tmp_path / "census"),
        evaluator=FixedEvaluator(),
    )
    repeated = restarted.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 5))
    assert repeated == first
    assert restarted.evaluate_outcome(
        advice["id"], evaluated_as_of=date(2026, 10, 6)
    ) == first
    second, _run, _ = _save_advice(restarted.history, symbol="2331.TWSE")
    restarted.evaluate_outcome(second["id"], evaluated_as_of=date(2026, 10, 5))
    assert "truncated" not in service.history.path.read_text(encoding="utf-8")

    restarted.evaluator = FixedEvaluator("not_triggered")
    with pytest.raises(AIResearchHistoryError, match="Conflicting terminal outcome"):
        restarted.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 7))


def test_statistics_threshold_zero_return_and_nontriggered_are_not_losses(tmp_path):
    service = _service(tmp_path)
    plan_identity = None
    returns = [10.0, 5.0, 1.0, 0.0]
    for index, value in enumerate(returns):
        advice, _run, plan = _save_advice(
            service.history,
            symbol=f"{2300 + index}.TWSE",
        )
        plan_identity = plan.plan_identity
        service.evaluator = FixedEvaluator("triggered", value)
        service.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 5))
    assert plan_identity is not None
    four = service.statistics(plan_identity)
    assert four.win_rate_denominator == 4
    assert four.hit_count == 3
    assert four.win_rate_pct is None and four.average_return_pct is None

    fifth, _run, _ = _save_advice(service.history, symbol="2400.TWSE")
    service.evaluator = FixedEvaluator("triggered", -2.0)
    service.evaluate_outcome(fifth["id"], evaluated_as_of=date(2026, 10, 5))
    no_entry, _run, _ = _save_advice(service.history, symbol="2401.TWSE")
    service.evaluator = FixedEvaluator("not_triggered")
    service.evaluate_outcome(no_entry["id"], evaluated_as_of=date(2026, 10, 5))
    missing, _run, _ = _save_advice(service.history, symbol="2402.TWSE")
    service.evaluator = FixedEvaluator("data_insufficient")
    service.evaluate_outcome(missing["id"], evaluated_as_of=date(2026, 10, 5))

    five = service.statistics(plan_identity)
    assert five.sample_sufficient is True
    assert five.win_rate_denominator == 5
    assert five.hit_count == 3
    assert five.win_rate_pct == 60
    assert five.average_return_pct == pytest.approx(2.8)
    assert five.outcome_status_counts["not_triggered"] == 1
    assert five.outcome_status_counts["data_insufficient"] == 1


def test_statistics_deduplicates_plan_instances_and_excludes_conflicts(tmp_path):
    service = _service(tmp_path, gross_return_pct=5.0)
    first, _run, plan = _save_advice(service.history, run_id="duplicate-one")
    second, _run, _ = _save_advice(service.history, run_id="duplicate-two")
    service.evaluate_outcome(second["id"], evaluated_as_of=date(2026, 10, 5))

    deduplicated = service.statistics(plan.plan_identity)
    assert deduplicated.admitted_plan_instances == 1
    assert deduplicated.duplicate_plan_instances == 1
    assert deduplicated.integrity_conflicts == 0

    service.evaluator = FixedEvaluator("triggered", -5.0)
    service.evaluate_outcome(first["id"], evaluated_as_of=date(2026, 10, 5))
    conflicted = service.statistics(plan.plan_identity)
    assert conflicted.admitted_plan_instances == 0
    assert conflicted.integrity_conflicts == 1
    assert conflicted.win_rate_denominator == 0


@pytest.mark.parametrize("tamper", ["gross_return", "outcome_digest"])
def test_outcome_tamper_is_rejected_by_statistics_and_reflection(tmp_path, tamper):
    service = _service(tmp_path)
    advice, _run, plan = _save_advice(service.history)
    outcome = service.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 5))
    rows = [json.loads(line) for line in service.history.path.read_text(encoding="utf-8").splitlines()]
    persisted = next(item for item in rows if item.get("id") == outcome["id"])
    if tamper == "gross_return":
        persisted["outcome"]["gross_return_pct"] = 999
    else:
        persisted["outcome_digest"] = "0" * 64
    service.history.path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    stats = service.statistics(plan.plan_identity)
    assert stats.integrity_conflicts == 1
    assert stats.win_rate_denominator == 0
    with pytest.raises(AIResearchHistoryError, match="outcome_digest_mismatch"):
        service.create_reflection(
            advice["id"],
            ReflectionCreate(outcome_id=outcome["id"], summary="must fail"),
        )


def test_reflections_are_immutable_cutoff_bound_same_plan_and_max_two(tmp_path):
    service = _service(tmp_path)
    created = []
    plan_identity = None
    for index, completed in enumerate((
        "2026-10-03T10:00:00+08:00",
        "2026-10-04T10:00:00+08:00",
        "2026-10-05T10:00:00+08:00",
    )):
        advice, _run, plan = _save_advice(
            service.history,
            symbol=f"{2500 + index}.TWSE",
        )
        plan_identity = plan.plan_identity
        with patch(
            "app.taiwan.advice_tracking.taipei_now",
            return_value=datetime.fromisoformat("2026-10-03T09:00:00+08:00"),
        ):
            outcome = service.evaluate_outcome(advice["id"], evaluated_as_of=date(2026, 10, 2))
        with patch(
            "app.taiwan.advice_tracking.taipei_now",
            return_value=datetime.fromisoformat(completed),
        ):
            created.append(service.create_reflection(
                advice["id"],
                ReflectionCreate(outcome_id=outcome["id"], summary=f"reflection {index}"),
            ))
    assert plan_identity is not None
    with (
        patch(
            "app.taiwan.advice_tracking.taipei_now",
            return_value=datetime.fromisoformat("2026-10-06T10:00:00+08:00"),
        ),
        pytest.raises(AIResearchHistoryError, match="different content"),
    ):
        service.create_reflection(
            created[0]["parent_id"],
            ReflectionCreate(outcome_id=created[0]["outcome_id"], summary="changed"),
        )

    selected = service.select_reflections(
        plan_identity,
        decision_cutoff="2026-10-06T09:00:00+08:00",
    )
    assert [item["summary"] for item in selected] == ["reflection 2", "reflection 1"]
    equal = service.select_reflections(
        plan_identity,
        decision_cutoff="2026-10-05T10:00:00+08:00",
    )
    assert [item["summary"] for item in equal] == ["reflection 1", "reflection 0"]
    assert service.select_reflections(plan_identity, decision_cutoff="2026-10-06T09:00:00") == []


def test_reflection_selection_orders_by_outcome_maturity_before_completion(tmp_path):
    service = _service(tmp_path)
    plan_identity = None
    cases = (
        (date(2026, 10, 2), "2026-10-06T10:00:00+08:00", "old-maturity"),
        (date(2026, 10, 3), "2026-10-05T10:00:00+08:00", "mid-maturity"),
        (date(2026, 10, 4), "2026-10-04T14:00:00+08:00", "new-maturity"),
    )
    for index, (terminal_day, reflection_completed, summary) in enumerate(cases):
        advice, _run, plan = _save_advice(
            service.history,
            symbol=f"{2600 + index}.TWSE",
        )
        plan_identity = plan.plan_identity
        service.evaluator = FixedEvaluator(terminal_day=terminal_day)
        with patch(
            "app.taiwan.advice_tracking.taipei_now",
            return_value=datetime.fromisoformat(
                f"{terminal_day.isoformat()}T14:00:00+08:00"
            ),
        ):
            outcome = service.evaluate_outcome(
                advice["id"], evaluated_as_of=terminal_day
            )
        with patch(
            "app.taiwan.advice_tracking.taipei_now",
            return_value=datetime.fromisoformat(reflection_completed),
        ):
            service.create_reflection(
                advice["id"],
                ReflectionCreate(outcome_id=outcome["id"], summary=summary),
            )

    selected = service.select_reflections(
        plan_identity,
        decision_cutoff="2026-10-07T09:00:00+08:00",
    )
    assert [item["summary"] for item in selected] == ["new-maturity", "mid-maturity"]


def test_tracking_api_is_thin_and_preserves_history_get_compatibility(tmp_path, monkeypatch):
    service = _service(tmp_path)
    advice, _run, plan = _save_advice(service.history)
    monkeypatch.setattr("app.api.taiwan.get_advice_tracking_service", lambda: service)
    monkeypatch.setattr("app.api.taiwan.get_ai_research_history_store", lambda: service.history)
    client = TestClient(app, client=("127.0.0.1", 50000))

    assert client.get(f"/api/taiwan/ai-research/history/{advice['id']}").status_code == 200
    admitted = client.get(
        f"/api/taiwan/ai-research/history/{advice['id']}/forward-status"
    )
    assert admitted.status_code == 200 and admitted.json()["status"] == "admitted"
    outcome = client.post(
        f"/api/taiwan/ai-research/history/{advice['id']}/evaluate-outcome",
        json={"evaluated_as_of": "2026-10-05", "view": "raw"},
    )
    assert outcome.status_code == 200 and outcome.json()["outcome"]["status"] == "triggered"
    stats = client.get(
        "/api/taiwan/ai-research/advice-statistics",
        params={"plan_identity": plan.plan_identity},
    )
    assert stats.status_code == 200 and stats.json()["win_rate_denominator"] == 1
