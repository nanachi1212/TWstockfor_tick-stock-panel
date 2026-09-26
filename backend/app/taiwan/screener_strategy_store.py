"""Taiwan Screener Strategy Persistence Service.

Stores custom user screening strategies in data/user_data/taiwan_screener_strategies.json.
Guaranteed to be private user data, excluded by .gitignore and never packaged into data bundles.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


class TaiwanScreenerStrategy(BaseModel):
    """User-saved or preset Taiwan screening strategy."""

    id: str
    name: str
    description: str | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    is_preset: bool = False
    created_at: str
    updated_at: str


PRESET_STRATEGIES: list[TaiwanScreenerStrategy] = [
    TaiwanScreenerStrategy(
        id="preset_revenue_growth_quant",
        name="營收成長+Quant",
        description="月營收 YoY 成長逾 20%、成交量能充裕且具備成長動能",
        conditions={
            "revenue_yoy_min": 20.0,
            "quant_score_min": 70.0,
            "volume_min": 500000.0,
            "net_income_positive": True,
        },
        is_preset=True,
        created_at="2026-09-26T00:00:00Z",
        updated_at="2026-09-26T00:00:00Z",
    ),
    TaiwanScreenerStrategy(
        id="preset_chip_improvement",
        name="法人籌碼改善",
        description="外資近20日持股比例正增長、當日外資買超且借券未出現異常暴增",
        conditions={
            "foreign_shareholding_change_20d_min": 0.0,
            "foreign_net_min": 0.0,
            "securities_lending_anomaly_exclude": True,
        },
        is_preset=True,
        created_at="2026-09-26T00:00:00Z",
        updated_at="2026-09-26T00:00:00Z",
    ),
]


def _storage_path() -> Path:
    p = settings.data_dir / "user_data" / "taiwan_screener_strategies.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class TaiwanScreenerStrategyStore:
    """Store for managing Taiwan screener custom strategies."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _storage_path()

    def list_strategies(self) -> list[TaiwanScreenerStrategy]:
        """List all strategies: built-in presets followed by saved user strategies."""
        user_strats: list[TaiwanScreenerStrategy] = []
        if self.path.exists():
            try:
                raw_list = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw_list, list):
                    for item in raw_list:
                        user_strats.append(TaiwanScreenerStrategy(**item))
            except Exception as e:
                logger.warning("Failed to load screener strategies from %s: %s", self.path, e)

        # Merge presets (avoid duplicate ID)
        user_ids = {s.id for s in user_strats}
        combined = [p for p in PRESET_STRATEGIES if p.id not in user_ids] + user_strats
        return combined

    def get_strategy(self, strategy_id: str) -> TaiwanScreenerStrategy | None:
        """Find strategy by ID."""
        for s in self.list_strategies():
            if s.id == strategy_id:
                return s
        return None

    def save_strategy(
        self,
        name: str,
        conditions: dict[str, Any],
        description: str | None = None,
        strategy_id: str | None = None,
    ) -> TaiwanScreenerStrategy:
        """Create or update a custom strategy."""
        now_iso = datetime.now(UTC).isoformat()
        current = self.list_strategies()
        user_strats = [s for s in current if not s.is_preset]

        if not strategy_id:
            import uuid
            strategy_id = f"strat_{uuid.uuid4().hex[:8]}"
            strat = TaiwanScreenerStrategy(
                id=strategy_id,
                name=name.strip(),
                description=description.strip() if description else None,
                conditions=conditions,
                is_preset=False,
                created_at=now_iso,
                updated_at=now_iso,
            )
            user_strats.append(strat)
        else:
            # Update existing
            found = False
            for i, s in enumerate(user_strats):
                if s.id == strategy_id:
                    strat = TaiwanScreenerStrategy(
                        id=strategy_id,
                        name=name.strip(),
                        description=description.strip() if description else None,
                        conditions=conditions,
                        is_preset=False,
                        created_at=s.created_at,
                        updated_at=now_iso,
                    )
                    user_strats[i] = strat
                    found = True
                    break
            if not found:
                strat = TaiwanScreenerStrategy(
                    id=strategy_id,
                    name=name.strip(),
                    description=description.strip() if description else None,
                    conditions=conditions,
                    is_preset=False,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
                user_strats.append(strat)

        self._persist(user_strats)
        return strat

    def delete_strategy(self, strategy_id: str) -> bool:
        """Delete custom user strategy by ID."""
        current = self.list_strategies()
        user_strats = [s for s in current if not s.is_preset]
        remaining = [s for s in user_strats if s.id != strategy_id]
        if len(remaining) == len(user_strats):
            return False  # Not found or was preset

        self._persist(remaining)
        return True

    def _persist(self, strategies: list[TaiwanScreenerStrategy]) -> None:
        payload = [s.model_dump() for s in strategies]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


_strategy_store_singleton: TaiwanScreenerStrategyStore | None = None


def get_screener_strategy_store() -> TaiwanScreenerStrategyStore:
    global _strategy_store_singleton
    if _strategy_store_singleton is None:
        _strategy_store_singleton = TaiwanScreenerStrategyStore()
    return _strategy_store_singleton
