"""Taiwan Universe and Security Master Package.

Exported classes:
  - TaiwanInstrument: Canonical instrument metadata model
  - UniverseType: Predefined market universe categories
  - MarketProfileBridge: Bridge to Phase 3 market profile rules
  - TwseInstrumentAdapter: Official TWSE open data adapter
  - TpexInstrumentAdapter: Official TPEx open data adapter
  - TaiwanSecurityMaster: Unified Security Master & Universe service
"""
from __future__ import annotations

from app.taiwan.universe.adapters import TpexInstrumentAdapter, TwseInstrumentAdapter
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument, UniverseType
from app.taiwan.universe.service import TaiwanSecurityMaster

# Module-level singleton
_default_master: TaiwanSecurityMaster | None = None


def get_security_master() -> TaiwanSecurityMaster:
    """Get or instantiate default TaiwanSecurityMaster."""
    global _default_master
    if _default_master is None:
        _default_master = TaiwanSecurityMaster()
    return _default_master


def reset_security_master() -> bool:
    """Reset or reload the default TaiwanSecurityMaster singleton. Returns True if successfully loaded."""
    global _default_master
    if _default_master is not None:
        return _default_master.reload()
    _default_master = TaiwanSecurityMaster()
    return _default_master.load_cache()


__all__ = [
    "MarketProfileBridge",
    "TaiwanInstrument",
    "TaiwanSecurityMaster",
    "TpexInstrumentAdapter",
    "TwseInstrumentAdapter",
    "UniverseType",
    "get_security_master",
    "reset_security_master",
]
