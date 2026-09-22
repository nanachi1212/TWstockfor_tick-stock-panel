"""Compatibility layer re-exporting app.rate_limits."""
from app import rate_limits as _rate_limits
from app.rate_limits import *  # noqa: F403

# `import *` skips underscore names; the shared slot table is part of the
# historical surface this shim replaced, so re-export it explicitly.
_next_slot = _rate_limits._next_slot
_slot_lock = _rate_limits._slot_lock
