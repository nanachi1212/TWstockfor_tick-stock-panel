"""Compatibility layer re-exporting app.rate_limits."""
from app.rate_limits import *  # noqa: F401,F403
# `import *` skips underscore names; re-export the process-local slot table
# explicitly so old-path callers/tests share the same state.
from app.rate_limits import _next_slot, _reserve_slot, _slot_lock  # noqa: F401
